"""V2.0 Phase 4.4 - user-facing download lifecycle controls."""
from __future__ import annotations

import asyncio

import pytest
from PySide6.QtTest import QTest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
from app.services.download_service import DownloadService


def _task(task_id="task-1", status=TaskStatus.QUEUED):
    task = DownloadTask(
        id=task_id,
        name="file.zip",
        source_url="https://example.com/page",
        download_url="https://example.com/file.zip",
        destination="/tmp/file.zip",
        status=status,
    )
    task.queue_position = 1
    return task


def _env(tmp_path, max_concurrent=1):
    destination = tmp_path / "downloads"
    destination.mkdir()
    manager = DownloadManager(
        max_concurrent=max_concurrent,
        allow_private_networks=True,
        downloads_dir=tmp_path,
    )
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None,
        download_manager=manager,
        queue_controller=controller,
        file_manager=FileManager(destination),
    )
    return manager, controller, service, destination


async def _submit(service, destination, name):
    return await service.start_file_download(
        source_url="https://example.com/page",
        file=DownloadFile(
            name=name,
            url=f"https://example.com/{name}",
            size=16,
            content_type="application/octet-stream",
        ),
        destination=str(destination),
        filename=name,
    )


async def _wait_for_status(task, status):
    for _ in range(150):
        if task.status is status:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"{task.id} did not reach {status}; got {task.status}")


def _visible_controls(card):
    return {
        name
        for name, button in (
            ("pause", card._pause_btn),
            ("resume", card._resume_btn),
            ("cancel", card._cancel_btn),
            ("retry", card._retry_btn),
        )
        if not button.isHidden()
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TaskStatus.QUEUED, {"cancel"}),
        (TaskStatus.DOWNLOADING, {"pause", "cancel"}),
        (TaskStatus.PAUSED, {"resume", "cancel"}),
        (TaskStatus.FAILED, {"retry"}),
        (TaskStatus.COMPLETED, set()),
        (TaskStatus.CANCELLED, set()),
    ],
)
def test_card_exposes_controls_for_authoritative_state(qapp, status, expected):
    from app.ui.widgets.download_card import DownloadCard

    card = DownloadCard(_task(status=status))
    QTest.qWait(1)
    assert _visible_controls(card) == expected


def test_card_updates_controls_after_status_transition(qapp):
    from app.ui.widgets.download_card import DownloadCard

    task = _task(status=TaskStatus.DOWNLOADING)
    card = DownloadCard(task)
    task.status = TaskStatus.PAUSED
    card.update_from_task(task)
    assert _visible_controls(card) == {"resume", "cancel"}


def test_card_actions_emit_stable_task_id(qapp):
    from app.ui.widgets.download_card import DownloadCard

    task = _task(task_id="stable-id", status=TaskStatus.DOWNLOADING)
    card = DownloadCard(task)
    emitted = []
    card.pause_requested.connect(emitted.append)
    card.cancel_requested.connect(emitted.append)
    card._pause_btn.click()
    card._cancel_btn.click()
    assert emitted == ["stable-id", "stable-id"]


@pytest.mark.asyncio
async def test_pause_resume_uses_one_task_and_two_executions(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    executions = []

    async def execute(task):
        executions.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        if len(executions) == 1:
            await asyncio.Event().wait()
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    task = await _submit(service, destination, "pause.zip")
    await _wait_for_status(task, TaskStatus.DOWNLOADING)
    service.pause_task(task.id)
    await _wait_for_status(task, TaskStatus.PAUSED)
    service.resume_task(task.id)
    await _wait_for_status(task, TaskStatus.COMPLETED)
    assert executions == [task.id, task.id]
    assert len(controller.tasks) == 1


@pytest.mark.asyncio
async def test_pause_frees_slot_for_next_queued_task(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    started = []
    first_hold = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        if task.name == "first.zip":
            await first_hold.wait()
        else:
            manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    first = await _submit(service, destination, "first.zip")
    second = await _submit(service, destination, "second.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)
    service.pause_task(first.id)
    await _wait_for_status(first, TaskStatus.PAUSED)
    await _wait_for_status(second, TaskStatus.COMPLETED)
    assert started == [first.id, second.id]
    first_hold.set()


@pytest.mark.asyncio
async def test_cancel_queued_updates_following_queue_position(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    hold = asyncio.Event()
    started = asyncio.Event()

    async def execute(task):
        manager._set_status(task, TaskStatus.DOWNLOADING)
        started.set()
        await hold.wait()

    monkeypatch.setattr(manager, "_execute_download", execute)
    active = await _submit(service, destination, "active.zip")
    await started.wait()
    await _wait_for_status(active, TaskStatus.DOWNLOADING)
    cancelled = await _submit(service, destination, "cancelled.zip")
    following = await _submit(service, destination, "following.zip")
    await _wait_for_status(cancelled, TaskStatus.QUEUED)
    service.cancel_task(cancelled.id)
    assert cancelled.status is TaskStatus.CANCELLED
    assert controller.get_queue_position(following.id) == 1
    hold.set()


@pytest.mark.asyncio
async def test_cancel_active_releases_slot_and_promotes_next(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    started = []
    hold = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        await hold.wait()
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    active = await _submit(service, destination, "active.zip")
    following = await _submit(service, destination, "following.zip")
    await _wait_for_status(active, TaskStatus.DOWNLOADING)
    service.cancel_task(active.id)
    await _wait_for_status(active, TaskStatus.CANCELLED)
    await _wait_for_status(following, TaskStatus.DOWNLOADING)
    hold.set()
    assert started == [active.id, following.id]


@pytest.mark.asyncio
async def test_repeated_cancel_is_once_only(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    started = asyncio.Event()

    async def execute(task):
        manager._set_status(task, TaskStatus.DOWNLOADING)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_execute_download", execute)
    task = await _submit(service, destination, "cancel-once.zip")
    await started.wait()
    await _wait_for_status(task, TaskStatus.DOWNLOADING)
    service.cancel_task(task.id)
    service.cancel_task(task.id)
    await _wait_for_status(task, TaskStatus.CANCELLED)
    assert controller.active_count == 0


@pytest.mark.asyncio
async def test_retry_reuses_id_and_obeys_concurrency(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    executions = []
    release = asyncio.Event()

    async def execute(task):
        executions.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        if len(executions) == 1:
            raise RuntimeError("temporary failure")
        await release.wait()
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    task = await _submit(service, destination, "retry.zip")
    blocker = await _submit(service, destination, "blocker.zip")
    await _wait_for_status(task, TaskStatus.FAILED)
    original_id = task.id
    service.retry_task(task.id)
    assert task.id == original_id
    assert len(controller.tasks) == 2
    assert executions.count(original_id) == 1
    release.set()
    await _wait_for_status(task, TaskStatus.COMPLETED)
    await _wait_for_status(blocker, TaskStatus.COMPLETED)
    assert executions.count(original_id) == 2


@pytest.mark.asyncio
async def test_repeated_retry_does_not_duplicate_execution(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    executions = []

    async def execute(task):
        executions.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        if len(executions) == 1:
            raise RuntimeError("temporary failure")
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    task = await _submit(service, destination, "retry-once.zip")
    await _wait_for_status(task, TaskStatus.FAILED)
    service.retry_task(task.id)
    service.retry_task(task.id)
    await _wait_for_status(task, TaskStatus.COMPLETED)
    assert executions == [task.id, task.id]
    assert len(controller.tasks) == 1


@pytest.mark.asyncio
async def test_page_wires_card_actions_to_public_controller(tmp_path, monkeypatch):
    manager, controller, service, destination = _env(tmp_path)
    from app.ui.pages.downloads_page import DownloadsPage

    page = DownloadsPage()
    page.set_queue_controller(controller, service)
    calls = []
    monkeypatch.setattr(controller, "pause_download", lambda task: calls.append(("pause", task.id)))
    monkeypatch.setattr(controller, "resume_download", lambda task: calls.append(("resume", task.id)))
    monkeypatch.setattr(controller, "cancel_download", lambda task: calls.append(("cancel", task.id)))
    monkeypatch.setattr(controller, "retry_download", lambda task_id: calls.append(("retry", task_id)))
    task = _task(task_id="page-id", status=TaskStatus.DOWNLOADING)
    manager.register_task(task)
    controller._emit_queue_change(task)
    page._cards[task.id]._pause_btn.click()
    page._cards[task.id]._cancel_btn.click()
    task.status = TaskStatus.PAUSED
    page._cards[task.id].update_from_task(task)
    page._cards[task.id]._resume_btn.click()
    task.status = TaskStatus.FAILED
    page._cards[task.id].update_from_task(task)
    page._cards[task.id]._retry_btn.click()
    assert calls == [
        ("pause", "page-id"),
        ("cancel", "page-id"),
        ("resume", "page-id"),
        ("retry", "page-id"),
    ]