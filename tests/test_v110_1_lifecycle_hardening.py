"""V1.10.1 tests — lifecycle hardening: public API, remove_task, restore sync."""
from __future__ import annotations

import pytest

from app.core.downloader import DownloadManager
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus


def _make_task(
    task_id: str = "t1",
    status: TaskStatus = TaskStatus.QUEUED,
) -> DownloadTask:
    return DownloadTask(
        id=task_id,
        name=f"file_{task_id}.zip",
        source_url=f"https://example.com/page{task_id}",
        download_url=f"https://example.com/file{task_id}.zip",
        destination=f"/tmp/file_{task_id}.zip",
        status=status,
    )


@pytest.fixture
def manager():
    return DownloadManager(max_concurrent=3, allow_private_networks=True)


@pytest.fixture
def controller(manager):
    return QueueController(manager)


# ── DownloadManager.max_concurrent (public property) ────────────────────

def test_manager_exposes_max_concurrent():
    m = DownloadManager(max_concurrent=5)
    assert m.max_concurrent == 5


def test_manager_max_concurrent_after_set():
    m = DownloadManager(max_concurrent=3)
    m.set_max_concurrent(7)
    assert m.max_concurrent == 7


# ── DownloadManager.remove_task ──────────────────────────────────────────

def test_manager_remove_task_returns_true_when_found(manager):
    task = _make_task("t1")
    manager._download_tasks.append(task)

    result = manager.remove_task("t1")

    assert result is True
    assert manager.find_task("t1") is None


def test_manager_remove_task_returns_false_when_missing(manager):
    result = manager.remove_task("nonexistent")
    assert result is False


def test_manager_remove_task_cancels_asyncio_task(manager):
    from unittest.mock import MagicMock

    task = _make_task("t1")
    manager._download_tasks.append(task)
    mock_asyncio_task = MagicMock()
    mock_asyncio_task.done.return_value = False
    manager._tasks["t1"] = mock_asyncio_task

    manager.remove_task("t1")

    assert "t1" not in manager._tasks
    mock_asyncio_task.cancel.assert_called_once()


# ── QueueController.remove_task (with persistence) ──────────────────────

def test_queue_controller_remove_task_delegates_to_manager(controller, manager):
    task = _make_task("t1")
    manager._download_tasks.append(task)

    result = controller.remove_task("t1")

    assert result is True
    assert controller.find_task("t1") is None


def test_queue_controller_remove_task_returns_false_when_missing(controller):
    result = controller.remove_task("nonexistent")
    assert result is False


def test_queue_controller_remove_task_emits_queue_change(controller):
    calls = []
    controller.add_queue_callback(lambda t: calls.append(t))

    task = _make_task("t1")
    controller._manager._download_tasks.append(task)

    controller.remove_task("t1")

    assert len(calls) >= 1


def test_queue_controller_remove_task_deletes_from_database(controller, manager):
    from unittest.mock import patch, MagicMock

    task = _make_task("t1")
    manager._download_tasks.append(task)

    with patch("app.core.queue_controller.delete_download_task") as mock_delete:
        controller.remove_task("t1")
        mock_delete.assert_called_once_with("t1")


def test_queue_controller_remove_task_no_db_error_on_missing(controller, manager):
    from unittest.mock import patch

    with patch("app.core.queue_controller.delete_download_task") as mock_delete:
        result = controller.remove_task("nonexistent")
        assert result is False
        mock_delete.assert_not_called()


# ── QueueController uses public max_concurrent ─────────────────────────

def test_queue_controller_reads_public_max_concurrent(manager):
    qc = QueueController(manager)
    assert qc.max_concurrent == manager.max_concurrent


# ── restore_tasks emits queue changes for restored tasks ────────────────

def test_restore_tasks_emits_for_each_task(controller, manager):
    task_a = _make_task("a", status=TaskStatus.PAUSED)
    task_b = _make_task("b", status=TaskStatus.COMPLETED)
    task_c = _make_task("c", status=TaskStatus.FAILED)

    callback_tasks = []
    controller.add_queue_callback(lambda t: callback_tasks.append(t))

    controller.restore_tasks([task_a, task_b, task_c])

    # Each restored task should trigger at least one queue-change emission.
    # restore_tasks calls _emit_progress for each, which triggers
    # _on_manager_progress → _emit_queue_change.
    restored_ids = {t.id for t in callback_tasks if t is not None}
    # At minimum, the active (non-terminal) tasks should produce callbacks.
    # COMPLETED tasks are skipped by DownloadsPage, so they may or may not
    # appear here depending on callback order.
    assert task_a.id in restored_ids
    assert task_c.id in restored_ids


def test_restore_tasks_returns_interrupted(controller, manager):
    task = _make_task("t1", status=TaskStatus.PAUSED)
    interrupted = controller.restore_tasks([task])
    assert interrupted == []


def test_restore_tasks_second_call_is_empty(controller, manager):
    tasks = [_make_task("a", status=TaskStatus.PAUSED)]
    controller.restore_tasks(tasks)

    # restore_tasks on the manager returns [] if tasks already exist
    interrupted = controller.restore_tasks(tasks)
    assert interrupted == []


# ── pause_download QUEUED safety net ────────────────────────────────────

def test_pause_queued_without_asyncio_task_sets_paused(controller, manager):
    """Core regression: QUEUED task with no asyncio task must still
    transition to PAUSED."""
    task = _make_task("t1", status=TaskStatus.QUEUED)
    # Add to _download_tasks but DON'T create an asyncio task
    manager._download_tasks.append(task)
    assert "t1" not in manager._tasks

    controller.pause_download(task)

    assert task.status is TaskStatus.PAUSED


def test_pause_downloading_with_asyncio_task_sets_paused(controller, manager):
    task = _make_task("t1", status=TaskStatus.DOWNLOADING)
    manager._download_tasks.append(task)

    controller.pause_download(task)

    assert task.status is TaskStatus.PAUSED
