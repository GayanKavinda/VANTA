"""V1.10 tests — QueueController lifecycle and queue management.

These tests verify the QueueController's queue management, pause/resume
for queued tasks, error re-classification, and queue-change callbacks
without requiring a live HTTP server.
"""
from __future__ import annotations

import pytest

from app.core.downloader import DownloadManager
from app.core.queue_controller import QueueController, classify_download_error
from app.core.task_manager import DownloadErrorType, DownloadTask, TaskStatus


def _make_task(
    task_id: str = "t1",
    status: TaskStatus = TaskStatus.QUEUED,
    speed: float = 0.0,
    total_size: int = 0,
    downloaded_size: int = 0,
    queue_order: int = 0,
    error: str | None = None,
    error_type=DownloadErrorType.UNKNOWN,
) -> DownloadTask:
    return DownloadTask(
        id=task_id,
        name=f"file_{task_id}.zip",
        source_url=f"https://example.com/page{task_id}",
        download_url=f"https://example.com/file{task_id}.zip",
        destination=f"/tmp/file_{task_id}.zip",
        status=status,
        speed=speed,
        total_size=total_size,
        downloaded_size=downloaded_size,
        queue_order=queue_order,
        error=error,
        error_type=error_type,
    )


@pytest.fixture
def manager():
    return DownloadManager(max_concurrent=3, allow_private_networks=True)


@pytest.fixture
def controller(manager):
    return QueueController(manager)


# ── properties ───────────────────────────────────────────────────────────

def test_available_slots_with_no_tasks(controller):
    assert controller.available_slots == 3
    assert controller.active_count == 0
    assert controller.queued_count == 0


def test_available_slots_decreases_with_active_tasks(controller, manager):
    manager._download_tasks = [
        _make_task("a", status=TaskStatus.DOWNLOADING),
        _make_task("b", status=TaskStatus.PREPARING),
    ]
    assert controller.active_count == 2
    assert controller.available_slots == 1


def test_queued_count_tracking(controller, manager):
    manager._download_tasks = [
        _make_task("a", status=TaskStatus.DOWNLOADING),
        _make_task("b", status=TaskStatus.QUEUED),
        _make_task("c", status=TaskStatus.QUEUED),
        _make_task("d", status=TaskStatus.PAUSED),
    ]
    assert controller.queued_count == 2
    assert controller.active_count == 1


def test_incomplete_tasks_excludes_terminal(controller, manager):
    manager._download_tasks = [
        _make_task("a", status=TaskStatus.DOWNLOADING),
        _make_task("b", status=TaskStatus.QUEUED),
        _make_task("c", status=TaskStatus.COMPLETED),
        _make_task("d", status=TaskStatus.FAILED),
        _make_task("e", status=TaskStatus.CANCELLED),
    ]
    incomplete = controller.incomplete_tasks
    ids = {t.id for t in incomplete}
    assert ids == {"a", "b"}


# ── pause_download for QUEUED tasks (the bug fix) ──────────────────────

def test_pause_queued_task_sets_paused(controller, manager):
    """The core regression: a QUEUED task with no live asyncio task
    must still transition to PAUSED."""
    task = _make_task("t1", status=TaskStatus.QUEUED)
    manager._download_tasks.append(task)

    controller.pause_download(task)

    assert task.status is TaskStatus.PAUSED
    assert task.error == "Download was paused"


def test_pause_downloading_task_delegates_to_manager(controller, manager):
    task = _make_task("t1", status=TaskStatus.DOWNLOADING)
    manager._download_tasks.append(task)

    controller.pause_download(task)

    assert task.status is TaskStatus.PAUSED


def test_pause_already_paused_is_noop(controller, manager):
    task = _make_task("t1", status=TaskStatus.PAUSED)
    manager._download_tasks.append(task)

    controller.pause_download(task)

    assert task.status is TaskStatus.PAUSED


def test_pause_terminal_task_is_noop(controller, manager):
    task = _make_task("t1", status=TaskStatus.COMPLETED)
    manager._download_tasks.append(task)

    controller.pause_download(task)

    assert task.status is TaskStatus.COMPLETED


# ── pause_all ────────────────────────────────────────────────────────────

def test_pause_all_includes_queued_tasks(controller, manager):
    tasks = [
        _make_task("a", status=TaskStatus.QUEUED),
        _make_task("b", status=TaskStatus.DOWNLOADING),
        _make_task("c", status=TaskStatus.QUEUED),
        _make_task("d", status=TaskStatus.PAUSED),
        _make_task("e", status=TaskStatus.COMPLETED),
    ]
    manager._download_tasks = tasks

    controller.pause_all()

    statuses = {t.id: t.status for t in tasks}
    assert statuses["a"] is TaskStatus.PAUSED
    assert statuses["b"] is TaskStatus.PAUSED
    assert statuses["c"] is TaskStatus.PAUSED
    assert statuses["d"] is TaskStatus.PAUSED
    assert statuses["e"] is TaskStatus.COMPLETED


# ── resume_download ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_paused_task(controller, manager):
    task = _make_task("t1", status=TaskStatus.PAUSED)
    manager._download_tasks.append(task)

    controller.resume_download(task)

    assert task.status is TaskStatus.QUEUED

    asyncio_task = manager._tasks.get(task.id)
    if asyncio_task and not asyncio_task.done():
        asyncio_task.cancel()


@pytest.mark.asyncio
async def test_resume_failed_task(controller, manager):
    task = _make_task("t1", status=TaskStatus.FAILED)
    manager._download_tasks.append(task)

    controller.resume_download(task)

    assert task.status is TaskStatus.QUEUED

    asyncio_task = manager._tasks.get(task.id)
    if asyncio_task and not asyncio_task.done():
        asyncio_task.cancel()


def test_resume_queued_task_is_noop(controller, manager):
    task = _make_task("t1", status=TaskStatus.QUEUED)
    manager._download_tasks.append(task)

    controller.resume_download(task)

    assert task.status is TaskStatus.QUEUED


def test_resume_completed_task_is_noop(controller, manager):
    task = _make_task("t1", status=TaskStatus.COMPLETED)
    manager._download_tasks.append(task)

    controller.resume_download(task)

    assert task.status is TaskStatus.COMPLETED


# ── queue change callbacks ───────────────────────────────────────────────

def test_queue_callback_called_on_add(controller, manager):
    received = []

    controller.add_queue_callback(lambda t: received.append(t))

    task = _make_task("t1", status=TaskStatus.DOWNLOADING)
    manager._download_tasks.append(task)

    controller._on_manager_progress(task)

    assert len(received) == 1
    assert received[0] is task


def test_queue_callback_called_on_pause(controller, manager):
    received = []
    controller.add_queue_callback(lambda t: received.append(t))

    task = _make_task("t1", status=TaskStatus.QUEUED)
    manager._download_tasks.append(task)

    controller.pause_download(task)

    assert len(received) >= 1


def test_queue_callback_called_on_bulk_pause_all(controller, manager):
    received = []
    controller.add_queue_callback(lambda t: received.append(t))

    tasks = [
        _make_task("a", status=TaskStatus.QUEUED),
        _make_task("b", status=TaskStatus.DOWNLOADING),
    ]
    manager._download_tasks = tasks

    controller.pause_all()

    received_non_none = [t for t in received if t is not None]
    assert len(received_non_none) >= 2


def test_queue_callback_not_called_after_removal(controller, manager):
    received = []

    def cb(t):
        received.append(t)

    controller.add_queue_callback(cb)
    controller.remove_queue_callback(cb)

    task = _make_task("t1", status=TaskStatus.DOWNLOADING)
    manager._download_tasks.append(task)
    controller._on_manager_progress(task)

    assert len(received) == 0


# ── error classification ─────────────────────────────────────────────────

def test_error_classification_on_failed_task(controller, manager):
    task = _make_task(
        "t1",
        status=TaskStatus.FAILED,
        error="Server returned an HTML page instead of 'file.zip' (Content-Type: text/html).",
    )
    assert task.error_type is DownloadErrorType.UNKNOWN

    controller._on_manager_progress(task)

    assert task.error_type is DownloadErrorType.HTML_RESPONSE


def test_error_classification_preserves_known_type(controller, manager):
    task = _make_task(
        "t1",
        status=TaskStatus.FAILED,
        error="Redirect loop detected (redirect_loop)",
        error_type=DownloadErrorType.REDIRECT_LOOP,
    )

    controller._on_manager_progress(task)

    assert task.error_type is DownloadErrorType.REDIRECT_LOOP


def test_error_classification_on_non_failed_is_noop(controller, manager):
    task = _make_task(
        "t1",
        status=TaskStatus.DOWNLOADING,
        error="Server returned an HTML page",
    )

    controller._on_manager_progress(task)

    assert task.error_type is DownloadErrorType.UNKNOWN


# ── set_max_concurrent ────────────────────────────────────────────────

def test_set_max_concurrent_updates_property(controller, manager):
    assert controller.max_concurrent == 3
    controller.set_max_concurrent(5)
    assert controller.max_concurrent == 5


def test_set_max_concurrent_propagates_to_manager(controller, manager):
    controller.set_max_concurrent(5)
    assert manager.max_concurrent == 5


def test_constructor_sets_initial_max_concurrent():
    manager = DownloadManager(max_concurrent=3, allow_private_networks=True)
    controller = QueueController(manager, max_concurrent=7)
    assert controller.max_concurrent == 7
    assert manager.max_concurrent == 7


def test_controller_has_no_separate_max_concurrent_attribute(controller):
    assert not hasattr(controller, "_max_concurrent")


def test_max_concurrent_change_updates_available_slots(controller, manager):
    manager._download_tasks = [
        _make_task("a", status=TaskStatus.DOWNLOADING),
        _make_task("b", status=TaskStatus.QUEUED),
    ]
    assert controller.available_slots == 2

    controller.set_max_concurrent(4)
    assert controller.available_slots == 3
    assert manager.max_concurrent == 4


# ── find_task / tasks ────────────────────────────────────────────────────

def test_find_task_returns_matching(controller, manager):
    task = _make_task("t1", status=TaskStatus.QUEUED)
    manager._download_tasks.append(task)

    found = controller.find_task("t1")
    assert found is task


def test_find_task_returns_none_for_missing(controller, manager):
    assert controller.find_task("nonexistent") is None


def test_tasks_returns_manager_list(controller, manager):
    task = _make_task("t1", status=TaskStatus.QUEUED)
    manager._download_tasks.append(task)

    assert controller.tasks is manager.download_tasks


# ── restore_tasks ────────────────────────────────────────────────────────

def test_restore_tasks_delegates_to_manager(controller):
    tasks = [_make_task("a", status=TaskStatus.PAUSED), _make_task("b", status=TaskStatus.COMPLETED)]
    interrupted = controller.restore_tasks(tasks)

    assert interrupted == []
    assert controller.find_task("a").status is TaskStatus.PAUSED
    assert controller.find_task("b").status is TaskStatus.COMPLETED


# ── cancel_download ──────────────────────────────────────────────────────

def test_cancel_download_terminal_is_noop(controller, manager):
    task = _make_task("t1", status=TaskStatus.COMPLETED)
    manager._download_tasks.append(task)

    controller.cancel_download(task)

    assert task.status is TaskStatus.COMPLETED


# ── async add_download ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_download_through_queue_controller(manager, tmp_path):
    controller = QueueController(manager)

    task = await controller.add_download(
        name="test.zip",
        source_url="https://example.com/page",
        download_url="https://example.com/test.zip",
        destination=str(tmp_path / "test.zip"),
    )

    assert task in manager.download_tasks
    assert task.status is TaskStatus.QUEUED
    assert manager.find_task(task.id) is task

    # Clean up: cancel the asyncio task to avoid warnings
    asyncio_task = manager._tasks.get(task.id)
    if asyncio_task and not asyncio_task.done():
        asyncio_task.cancel()
