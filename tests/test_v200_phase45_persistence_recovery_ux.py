"""V2.0 Phase 4.5 - persistence and recovery UX coverage."""
from __future__ import annotations

import asyncio

import pytest
from PySide6.QtTest import QTest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadErrorType, DownloadTask, TaskStatus
from app.database.connection import get_session
from app.database.models import DownloadRecord
from app.database.repositories import load_download_tasks, save_download_task
from app.services.crash_recovery import CrashRecovery
from app.services.download_service import DownloadService
from app.services.persistence_service import PersistenceService


def _task(tmp_path, task_id, status=TaskStatus.QUEUED, queue_order=0, **kwargs):
    return DownloadTask(
        id=task_id,
        name=f"{task_id}.zip",
        source_url="https://example.com/page",
        download_url=f"https://cdn.example.com/{task_id}.zip",
        destination=str(tmp_path / f"{task_id}.zip"),
        status=status,
        queue_order=queue_order,
        **kwargs,
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


def test_task_round_trip_preserves_identity_and_metadata(tmp_path):
    task = _task(
        tmp_path,
        "phase45_roundtrip",
        status=TaskStatus.FAILED,
        queue_order=7,
        total_size=4096,
        downloaded_size=2048,
        progress=50.0,
        speed=128.0,
        error="network timeout",
        error_type=DownloadErrorType.TIMEOUT,
        supports_resume=True,
    )
    task.created_at = 100.0
    task.updated_at = 200.0
    save_download_task(task)
    try:
        [loaded] = [t for t in load_download_tasks() if t.id == task.id]
        assert loaded.id == task.id
        assert loaded.name == task.name
        assert loaded.source_url == task.source_url
        assert loaded.download_url == task.download_url
        assert loaded.destination == task.destination
        assert loaded.status is TaskStatus.FAILED
        assert loaded.queue_order == 7
        assert loaded.created_at == 100.0
        assert loaded.updated_at == 200.0
        assert loaded.downloaded_size == 2048
        assert loaded.total_size == 4096
        assert loaded.progress == 50.0
        assert loaded.error == "network timeout"
        assert loaded.error_type is DownloadErrorType.TIMEOUT
        assert loaded.supports_resume is True
    finally:
        _cleanup([task.id])


def test_persistence_flush_writes_latest_authoritative_state(tmp_path):
    manager = DownloadManager(max_concurrent=1, allow_private_networks=True)
    persistence = PersistenceService()
    persistence.subscribe_to(manager)
    task = _task(tmp_path, "phase45_flush")
    manager.register_task(task)
    task.downloaded_size = 321
    task.total_size = 1000
    task.progress = 32.1
    task.status = TaskStatus.PAUSED
    persistence.flush()
    try:
        loaded = next(t for t in load_download_tasks() if t.id == task.id)
        assert loaded.status is TaskStatus.PAUSED
        assert loaded.downloaded_size == 321
        assert loaded.progress == 32.1
    finally:
        _cleanup([task.id])


def test_recovery_preserves_zero_based_queue_order(tmp_path):
    tasks = [
        _task(tmp_path, "phase45_a", queue_order=0),
        _task(tmp_path, "phase45_b", queue_order=1),
        _task(tmp_path, "phase45_c", queue_order=2),
    ]
    recovered, _ = CrashRecovery(tmp_path).recover(tasks)
    assert [task.id for task in recovered] == [
        "phase45_a", "phase45_b", "phase45_c"
    ]


@pytest.mark.asyncio
async def test_restored_queued_tasks_are_admitted_once_in_order(tmp_path, monkeypatch):
    manager, controller, _, _ = _env(tmp_path)
    started = []
    releases = [asyncio.Event() for _ in range(3)]

    async def execute(task):
        started.append(task.id)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        await releases[len(started) - 1].wait()
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    tasks = [_task(tmp_path, f"phase45_queue_{i}", queue_order=i) for i in range(3)]
    controller.restore_tasks(tasks)
    controller.restore_tasks(tasks)
    await _wait_for_status(tasks[0], TaskStatus.DOWNLOADING)
    assert started == [tasks[0].id]
    assert len(controller.tasks) == 3
    releases[0].set()
    await _wait_for_status(tasks[0], TaskStatus.COMPLETED)
    await _wait_for_status(tasks[1], TaskStatus.DOWNLOADING)
    releases[1].set()
    await _wait_for_status(tasks[1], TaskStatus.COMPLETED)
    await _wait_for_status(tasks[2], TaskStatus.DOWNLOADING)
    releases[2].set()
    await _wait_for_status(tasks[2], TaskStatus.COMPLETED)
    assert started == [task.id for task in tasks]


@pytest.mark.asyncio
async def test_restored_queue_respects_concurrency_limit(tmp_path, monkeypatch):
    manager, controller, _, _ = _env(tmp_path, max_concurrent=2)
    active = 0
    peak = 0
    release = asyncio.Event()

    async def execute(task):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        manager._set_status(task, TaskStatus.DOWNLOADING)
        await release.wait()
        active -= 1
        manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(manager, "_execute_download", execute)
    tasks = [_task(tmp_path, f"phase45_limit_{i}", queue_order=i) for i in range(3)]
    controller.restore_tasks(tasks)
    await _wait_for_status(tasks[0], TaskStatus.DOWNLOADING)
    await _wait_for_status(tasks[1], TaskStatus.DOWNLOADING)
    assert peak == 2
    assert tasks[2].status is TaskStatus.QUEUED
    release.set()
    for task in tasks:
        await _wait_for_status(task, TaskStatus.COMPLETED)


@pytest.mark.parametrize(
    "status",
    [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED],
)
def test_terminal_tasks_restore_without_scheduler_admission(tmp_path, status):
    manager, controller, _, _ = _env(tmp_path, max_concurrent=0)
    task = _task(tmp_path, f"phase45_{status.value}", status=status)
    manager.restore_tasks([task])
    assert manager.find_task(task.id) is task
    assert task.status is status
    assert controller.active_count == 0
    assert not controller._scheduler.is_task_scheduled(task.id)


def test_interrupted_task_becomes_paused_with_identity_and_destination(tmp_path):
    task = _task(
        tmp_path,
        "phase45_interrupted",
        status=TaskStatus.DOWNLOADING,
        downloaded_size=128,
        total_size=512,
        progress=25.0,
    )
    recovered, report = CrashRecovery(tmp_path).recover([task])
    [restored] = recovered
    assert restored.id == task.id
    assert restored.destination == task.destination
    assert restored.name == task.name
    assert restored.status is TaskStatus.PAUSED
    assert restored.error_type is DownloadErrorType.NETWORK
    assert "interrupted" in restored.error.lower()
    assert report.interrupted_restarted == 1


def test_interrupted_partial_file_is_validated_and_retained(tmp_path):
    task = _task(
        tmp_path,
        "phase45_partial",
        status=TaskStatus.DOWNLOADING,
        downloaded_size=4,
    )
    part_path = tmp_path / "phase45_partial.zip.part"
    part_path.write_bytes(b"data")
    recovered, _ = CrashRecovery(tmp_path).recover([task])
    [restored] = recovered
    assert restored.status is TaskStatus.PAUSED
    assert part_path.exists()
    assert restored.downloaded_size == 4


def test_paused_task_restores_without_auto_start(tmp_path):
    task = _task(
        tmp_path,
        "phase45_paused",
        status=TaskStatus.PAUSED,
        downloaded_size=0,
        progress=0.0,
    )
    manager = DownloadManager(max_concurrent=1, allow_private_networks=True, downloads_dir=tmp_path)
    interrupted = manager.restore_tasks([task])
    assert interrupted == []
    assert manager.find_task(task.id) is task
    assert task.status is TaskStatus.PAUSED


def test_recovery_does_not_duplicate_manager_tasks(tmp_path):
    manager = DownloadManager(max_concurrent=1, allow_private_networks=True, downloads_dir=tmp_path)
    task = _task(tmp_path, "phase45_duplicate")
    manager.restore_tasks([task])
    manager.restore_tasks([task])
    assert [t.id for t in manager.download_tasks].count(task.id) == 1


def test_database_validation_and_fix_preserve_recoverable_record(tmp_path):
    from app.database.repositories import fix_database_issues, validate_database

    task = _task(tmp_path, "phase45_validation")
    save_download_task(task)
    session = get_session()
    try:
        record = session.get(DownloadRecord, task.id)
        record.progress = 150.0
        session.commit()
    finally:
        session.close()
    try:
        assert validate_database().issues_found >= 1
        fix_database_issues()
        loaded = next(t for t in load_download_tasks() if t.id == task.id)
        assert loaded.progress == 0.0
    finally:
        _cleanup([task.id])


def test_downloads_page_creates_one_authoritative_card_per_restored_task(qapp, tmp_path):
    from app.ui.pages.downloads_page import DownloadsPage

    manager, controller, _, _ = _env(tmp_path, max_concurrent=0)
    page = DownloadsPage()
    page.set_queue_controller(controller)
    tasks = [
        _task(tmp_path, "phase45_card_queued", queue_order=0),
        _task(tmp_path, "phase45_card_failed", status=TaskStatus.FAILED),
        _task(tmp_path, "phase45_card_done", status=TaskStatus.COMPLETED),
    ]
    controller.restore_tasks(tasks)
    controller._emit_queue_change(None)
    QTest.qWait(1)
    assert set(page._cards) == {task.id for task in tasks}
    assert len(page._cards) == 3
    assert page._cards[tasks[0].id]._cancel_btn.isHidden() is False
    assert page._cards[tasks[1].id]._retry_btn.isHidden() is False
    assert page._cards[tasks[2].id]._cancel_btn.isHidden() is True


def test_restored_queue_positions_are_authoritative(tmp_path):
    manager = DownloadManager(max_concurrent=0, allow_private_networks=True, downloads_dir=tmp_path)
    controller = QueueController(manager)
    tasks = [_task(tmp_path, f"phase45_position_{i}", queue_order=i) for i in range(3)]
    controller.restore_tasks(tasks)
    assert [controller.get_queue_position(task.id) for task in tasks] == [1, 2, 3]


def test_failed_error_information_and_retry_state_survive_restart(tmp_path):
    task = _task(
        tmp_path,
        "phase45_failed",
        status=TaskStatus.FAILED,
        error="connection reset",
        error_type=DownloadErrorType.CONNECTION_INTERRUPTED,
        supports_resume=True,
    )
    save_download_task(task)
    try:
        loaded = next(t for t in load_download_tasks() if t.id == task.id)
        assert loaded.status is TaskStatus.FAILED
        assert loaded.error == "connection reset"
        assert loaded.error_type is DownloadErrorType.CONNECTION_INTERRUPTED
        assert loaded.supports_resume is True
    finally:
        _cleanup([task.id])