"""V2.0 Phase 4.1.4 - execution lifecycle integration coverage.

These tests begin at the reviewed-download service boundary and exercise the
existing QueueController, Scheduler, and DownloadManager lifecycle without
manager task creation, status callbacks, cancellation, and scheduler
promotion remain real.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadErrorType, TaskStatus
from app.services.download_service import DownloadService


class _Env:
    def __init__(self, manager, controller, service, destination):
        self.manager = manager
        self.controller = controller
        self.service = service
        self.destination = destination


@pytest.fixture
def env(tmp_path):
    destination = tmp_path / "downloads"
    destination.mkdir()
    manager = DownloadManager(
        max_concurrent=1,
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
    result = _Env(manager, controller, service, destination)
    yield result


def _file(name: str) -> DownloadFile:
    return DownloadFile(
        name=name,
        url=f"https://example.com/{name}",
        size=16,
        content_type="application/octet-stream",
    )


async def _submit(env: _Env, name: str):
    return await env.service.start_file_download(
        source_url="https://example.com/reviewed-page",
        file=_file(name),
        destination=str(env.destination),
        filename=name,
    )


async def _wait_for_status(task, status: TaskStatus):
    for _ in range(100):
        if task.status is status:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"{task.id} did not reach {status}; got {task.status}")


async def _wait_for_all(tasks):
    for task in tasks:
        while not task.is_terminal:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_reviewed_download_reaches_terminal_state(env, monkeypatch):
    async def complete(task):
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", complete)
    task = await _submit(env, "reviewed.zip")
    await _wait_for_status(task, TaskStatus.COMPLETED)

    assert task.name == "reviewed.zip"
    assert Path(task.destination).parent == env.destination
    assert env.controller.find_task(task.id) is task
    assert env.controller.active_count == 0
    assert not env.controller._scheduler.is_task_scheduled(task.id)


@pytest.mark.asyncio
async def test_completion_releases_slot_and_promotes_next(env, monkeypatch):
    started = []
    release_first = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        if len(started) == 1:
            await release_first.wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    first = await _submit(env, "a.zip")
    second = await _submit(env, "b.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)
    assert second.status is TaskStatus.QUEUED

    release_first.set()
    await _wait_for_all((first, second))

    assert started == [first.id, second.id]
    assert all(task.status is TaskStatus.COMPLETED for task in (first, second))
    assert env.controller.active_count == 0
    assert env.controller.available_slots == 1


@pytest.mark.asyncio
async def test_failure_releases_slot_and_preserves_error(env, monkeypatch):
    started = []

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        if task.name == "bad.zip":
            task.error_type = DownloadErrorType.NETWORK
            raise RuntimeError("controlled network failure")
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    failed = await _submit(env, "bad.zip")
    next_task = await _submit(env, "next.zip")
    await _wait_for_all((failed, next_task))

    assert failed.status is TaskStatus.FAILED
    assert failed.error == "controlled network failure"
    assert failed.error_type is DownloadErrorType.NETWORK
    assert next_task.status is TaskStatus.COMPLETED
    assert started == [failed.id, next_task.id]
    assert env.controller.active_count == 0
    assert not env.controller._scheduler.is_task_scheduled(failed.id)


@pytest.mark.asyncio
async def test_queued_cancellation_does_not_consume_slot(env, monkeypatch):
    release_first = asyncio.Event()
    started = []

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        await release_first.wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    first = await _submit(env, "active.zip")
    queued = await _submit(env, "cancelled.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    env.service.cancel_task(queued.id)
    assert queued.status is TaskStatus.CANCELLED
    assert queued.id not in started
    assert env.controller.active_count == 1

    release_first.set()
    await _wait_for_status(first, TaskStatus.COMPLETED)
    assert env.controller.active_count == 0


@pytest.mark.asyncio
async def test_active_cancellation_releases_slot_and_promotes_next(env, monkeypatch):
    started = []
    hold = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        await hold.wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    active = await _submit(env, "active.zip")
    next_task = await _submit(env, "next.zip")
    await _wait_for_status(active, TaskStatus.DOWNLOADING)

    env.service.cancel_task(active.id)
    await _wait_for_status(active, TaskStatus.CANCELLED)
    await _wait_for_status(next_task, TaskStatus.DOWNLOADING)
    hold.set()
    await _wait_for_status(next_task, TaskStatus.COMPLETED)

    assert started == [active.id, next_task.id]
    assert env.controller.active_count == 0
    assert not env.controller._scheduler.is_task_scheduled(active.id)


@pytest.mark.asyncio
async def test_pause_resume_does_not_duplicate_execution(env, monkeypatch):
    executions = []

    async def execute(task):
        executions.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        await asyncio.sleep(0)
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    task = await _submit(env, "pause-resume.zip")
    await _wait_for_status(task, TaskStatus.DOWNLOADING)
    env.service.pause_task(task.id)
    await _wait_for_status(task, TaskStatus.PAUSED)

    env.service.resume_task(task.id)
    await _wait_for_status(task, TaskStatus.COMPLETED)

    assert executions == [task.id, task.id]
    assert len(env.controller.tasks) == 1
    assert env.controller.active_count == 0


@pytest.mark.asyncio
async def test_retry_reuses_task_id_and_reenters_once(env, monkeypatch):
    executions = []

    async def execute(task):
        executions.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        if len(executions) == 1:
            raise RuntimeError("temporary failure")
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    task = await _submit(env, "retry.zip")
    await _wait_for_status(task, TaskStatus.FAILED)
    await asyncio.sleep(0)
    task.error_type = DownloadErrorType.NETWORK
    original_id = task.id

    env.service.retry_task(task.id)
    await _wait_for_status(task, TaskStatus.COMPLETED)

    assert task.id == original_id
    assert executions == [original_id, original_id]
    assert len(env.controller.tasks) == 1
    assert env.controller.active_count == 0


@pytest.mark.asyncio
async def test_retry_non_failed_task_does_not_reserve_scheduler_slot(env):
    task = await _submit(env, "not-failed.zip")
    scheduled_before = env.controller._scheduler.is_task_scheduled(task.id)
    active_before = env.controller.active_count
    env.controller._scheduler.stop()

    env.controller.retry_download(task.id)

    assert task.status is TaskStatus.QUEUED
    assert env.controller._scheduler.is_task_scheduled(task.id) is scheduled_before
    assert env.controller.active_count == active_before


@pytest.mark.asyncio
async def test_terminal_task_submission_is_not_an_active_duplicate(env, monkeypatch):
    executions = []

    async def execute(task):
        executions.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    first = await _submit(env, "again.zip")
    await _wait_for_status(first, TaskStatus.COMPLETED)
    second = await _submit(env, "again.zip")
    await _wait_for_status(second, TaskStatus.COMPLETED)

    assert second.id != first.id
    assert executions == [first.id, second.id]
    assert len(env.controller.tasks) == 2


@pytest.mark.asyncio
async def test_mixed_terminal_outcomes_drain_in_queue_order(env, monkeypatch):
    started = []
    release_a = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        if task.name == "a.zip":
            await release_a.wait()
            env.manager._set_status(task, TaskStatus.COMPLETED)
        elif task.name == "b.zip":
            raise RuntimeError("planned failure")
        else:
            env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    task_a = await _submit(env, "a.zip")
    task_b = await _submit(env, "b.zip")
    task_c = await _submit(env, "c.zip")
    task_d = await _submit(env, "d.zip")
    await _wait_for_status(task_a, TaskStatus.DOWNLOADING)
    env.service.cancel_task(task_c.id)
    release_a.set()
    await _wait_for_all((task_a, task_b, task_c, task_d))

    assert started == [task_a.id, task_b.id, task_d.id]
    assert task_a.status is TaskStatus.COMPLETED
    assert task_b.status is TaskStatus.FAILED
    assert task_c.status is TaskStatus.CANCELLED
    assert task_d.status is TaskStatus.COMPLETED
    assert env.controller.active_count == 0
    assert env.controller.queued_count == 0
    assert len({task.id for task in env.controller.tasks}) == 4


@pytest.mark.asyncio
async def test_reviewed_submission_is_exactly_once_while_queued(env, monkeypatch):
    started = []
    release = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        await release.wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)
    first = await _submit(env, "once.zip")
    duplicate = await _submit(env, "once.zip")
    assert duplicate is first
    assert len(env.controller.tasks) == 1

    release.set()
    await _wait_for_status(first, TaskStatus.COMPLETED)
    assert started == [first.id]
    assert not env.controller._scheduler.is_task_scheduled(first.id)
