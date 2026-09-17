"""V2.0 Phase 4.9 - persistent one-time download scheduling."""
from __future__ import annotations

import asyncio
import time

import pytest
from PySide6.QtCore import QDateTime
from PySide6.QtTest import QTest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
from app.database.connection import get_session
from app.database.models import DownloadRecord
from app.database.repositories import load_download_tasks, save_download_task
from app.services.download_service import DownloadService
from app.services.scheduling_service import SchedulingService


def _task(tmp_path, task_id, scheduled_at=None, status=TaskStatus.QUEUED, queue_order=0):
    return DownloadTask(
        id=task_id,
        name=f"{task_id}.zip",
        source_url="https://example.com/page",
        download_url=f"https://example.com/{task_id}.zip",
        destination=str(tmp_path / f"{task_id}.zip"),
        status=status,
        queue_order=queue_order,
        scheduled_at=scheduled_at,
    )


def _cleanup(task_ids):
    session = get_session()
    try:
        session.query(DownloadRecord).filter(
            DownloadRecord.id.in_(task_ids)
        ).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


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


async def _wait_for_status(task, status):
    for _ in range(150):
        if task.status is status:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"{task.id} did not reach {status}; got {task.status}")


def test_task_scheduled_at_defaults_to_none_and_round_trips(tmp_path):
    immediate = _task(tmp_path, "phase49_immediate")
    assert immediate.scheduled_at is None
    scheduled = _task(tmp_path, "phase49_roundtrip", scheduled_at=1234567890.5)
    save_download_task(scheduled)
    try:
        loaded = next(t for t in load_download_tasks() if t.id == scheduled.id)
        assert loaded.scheduled_at == 1234567890.5
    finally:
        _cleanup([scheduled.id])


def test_legacy_record_without_schedule_loads_as_immediate(tmp_path):
    task = _task(tmp_path, "phase49_legacy")
    save_download_task(task)
    session = get_session()
    try:
        record = session.get(DownloadRecord, task.id)
        record.scheduled_at = None
        session.commit()
    finally:
        session.close()
    try:
        loaded = next(t for t in load_download_tasks() if t.id == task.id)
        assert loaded.scheduled_at is None
    finally:
        _cleanup([task.id])


@pytest.mark.asyncio
async def test_future_schedule_is_registered_but_not_admitted(tmp_path):
    manager, controller, service, destination = _env(tmp_path, max_concurrent=1)
    task = await service.start_file_download(
        source_url="https://example.com/page",
        file=DownloadFile(
            name="future.zip",
            url="https://example.com/future.zip",
            size=1,
            content_type="application/zip",
        ),
        destination=str(destination),
        filename="future.zip",
        scheduled_at=time.time() + 3600,
    )
    assert task.scheduled_at is not None
    assert task.status is TaskStatus.QUEUED
    assert controller.active_count == 0
    assert not controller._scheduler.is_task_scheduled(task.id)


@pytest.mark.asyncio
async def test_due_schedule_uses_normal_queue_admission(tmp_path, monkeypatch):
    manager, controller, _, _ = _env(tmp_path, max_concurrent=1)
    started = asyncio.Event()

    async def execute(task):
        manager._set_status(task, TaskStatus.DOWNLOADING)
        started.set()
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    task = _task(tmp_path, "phase49_due", scheduled_at=time.time() - 1)
    controller.restore_tasks([task])
    assert task.scheduled_at is None
    await started.wait()
    await _wait_for_status(task, TaskStatus.COMPLETED)
    assert controller.active_count == 0


@pytest.mark.asyncio
async def test_scheduling_service_exact_due_and_no_duplicate_activation(tmp_path):
    now = [100.0]
    manager, controller, _, _ = _env(tmp_path, max_concurrent=0)
    task = _task(tmp_path, "phase49_timer", scheduled_at=101.0)
    manager.register_task(task)
    service = SchedulingService(controller, clock=lambda: now[0])
    assert await service.process_due_tasks() == 0
    assert task.scheduled_at == 101.0
    now[0] = 101.0
    assert await service.process_due_tasks() == 1
    assert await service.process_due_tasks() == 0
    assert task.scheduled_at is None


@pytest.mark.asyncio
async def test_cancelled_scheduled_task_never_activates(tmp_path):
    manager, controller, _, _ = _env(tmp_path, max_concurrent=1)
    task = _task(tmp_path, "phase49_cancel", scheduled_at=time.time() + 100)
    manager.register_task(task)
    controller.cancel_download(task)
    task.scheduled_at = time.time() - 1
    service = SchedulingService(controller)
    assert await service.process_due_tasks() == 0
    assert task.status is TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_due_task_waits_for_existing_concurrency_slot(tmp_path, monkeypatch):
    manager, controller, _, _ = _env(tmp_path, max_concurrent=1)
    hold = asyncio.Event()
    started = []

    async def execute(task):
        started.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        await hold.wait()
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    active = _task(tmp_path, "phase49_active")
    manager.register_task(active)
    controller._scheduler.on_task_added(active)
    due = _task(tmp_path, "phase49_due_wait", scheduled_at=time.time() - 1, queue_order=1)
    manager.register_task(due)
    service = SchedulingService(controller)
    assert await service.process_due_tasks() == 1
    await _wait_for_status(active, TaskStatus.DOWNLOADING)
    assert due.scheduled_at is None
    assert due.status is TaskStatus.QUEUED
    assert started == [active.id]
    hold.set()
    await _wait_for_status(due, TaskStatus.COMPLETED)
    assert started == [active.id, due.id]


def test_review_dialog_schedule_control_returns_timestamp(qapp, tmp_path):
    from app.ui.download_review import DownloadReviewDialog
    from app.services.download_workflow import DownloadWorkflowService

    destination = tmp_path / "downloads"
    destination.mkdir()
    manager = FileManager(destination)
    dialog = DownloadReviewDialog(
        file=DownloadFile(
            name="scheduled.zip",
            url="https://example.com/scheduled.zip",
            size=1,
            content_type="application/zip",
        ),
        file_manager=manager,
        download_dir=destination,
        workflow=DownloadWorkflowService(manager),
    )
    assert dialog.scheduled_at is None
    dialog._schedule_check.setChecked(True)
    future = QDateTime.currentDateTime().addSecs(600)
    dialog._schedule_edit.setDateTime(future)
    assert dialog.scheduled_at == future.toSecsSinceEpoch()


def test_scheduled_card_shows_start_time(qapp, tmp_path):
    from app.ui.widgets.download_card import DownloadCard

    task = _task(tmp_path, "phase49_card", scheduled_at=time.time() + 3600)
    card = DownloadCard(task)
    QTest.qWait(1)
    assert "Scheduled" in card._status_label.text()
    assert "Starts at" in card._status_label.text()


@pytest.mark.asyncio
async def test_scheduling_service_start_stop_is_single_managed_task(tmp_path):
    _, controller, _, _ = _env(tmp_path, max_concurrent=0)
    service = SchedulingService(controller)
    service.start()
    first = service._timer_task
    service.start()
    assert service._timer_task is first
    assert service.running
    service.stop()
    assert not service.running