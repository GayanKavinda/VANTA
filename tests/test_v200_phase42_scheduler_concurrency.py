"""V2.0 Phase 4.2 — Scheduler & Concurrency Integration.

Validates that the existing scheduler/concurrency contract correctly enforces and
adapts to the configured max_concurrent limit while preserving queue integrity and
lifecycle correctness. Tests cover dynamic concurrency changes (increase/decrease/
recovery), queue ordering preservation, and pause/cancel/failure/retry interactions
with concurrency transitions.

Tests are network-free: use an _execute_download shim with asyncio.Event synchronization
for deterministic execution.

Does NOT duplicate Phase 4.1 tests. Focuses on concurrency transitions, not individual
lifecycle states (covered in 4.1.4/4.1.5).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import TaskStatus
from app.services.download_service import DownloadService


class _Env:
    def __init__(self, manager, controller, service, destination):
        self.manager = manager
        self.controller = controller
        self.service = service
        self.destination = destination


def _make_env(tmp_path, max_concurrent=2) -> _Env:
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
    return _Env(manager, controller, service, destination)


def _file(name: str) -> DownloadFile:
    return DownloadFile(
        name=name,
        url=f"https://example.com/{name}",
        size=16,
        content_type="application/octet-stream",
    )


async def _submit(env: _Env, name: str):
    return await env.service.start_file_download(
        source_url="https://example.com/page",
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


@pytest.fixture
def env(tmp_path):
    result = _make_env(tmp_path, max_concurrent=2)
    yield result


@pytest.fixture
def env1(tmp_path):
    result = _make_env(tmp_path, max_concurrent=1)
    yield result


@pytest.fixture
def env3(tmp_path):
    result = _make_env(tmp_path, max_concurrent=3)
    yield result


# ── Concurrency = 1: only one executing task ────────────────────────────────

@pytest.mark.asyncio
async def test_concurrency_one_single_execution(env1, monkeypatch):
    started: list[str] = []
    release = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await release.wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "a.zip")
    second = await _submit(env1, "b.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    assert len(started) == 1
    assert started == [first.id]
    assert second.status is TaskStatus.QUEUED
    assert env1.controller.active_count == 1

    release.set()
    await _wait_for_all((first, second))
    assert started == [first.id, second.id]
    assert env1.controller.active_count == 0


# ── Concurrency = 2: allows parallel execution ────────────────────────────────

@pytest.mark.asyncio
async def test_concurrency_two_allows_parallel(env, monkeypatch):
    started: list[str] = []
    releases: dict[str, asyncio.Event] = {}

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        await releases[task.id].wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)

    first = await _submit(env, "a.zip")
    second = await _submit(env, "b.zip")
    third = await _submit(env, "c.zip")

    releases[first.id] = asyncio.Event()
    releases[second.id] = asyncio.Event()

    await _wait_for_status(first, TaskStatus.DOWNLOADING)
    await _wait_for_status(second, TaskStatus.DOWNLOADING)

    assert len(started) == 2
    assert started == [first.id, second.id]
    assert third.status is TaskStatus.QUEUED

    releases[third.id] = asyncio.Event()
    releases[first.id].set()
    releases[second.id].set()

    await _wait_for_status(third, TaskStatus.DOWNLOADING)
    releases[third.id].set()
    await _wait_for_all((first, second, third))
    assert started == [first.id, second.id, third.id]


# ── Concurrency never exceeds configured limit ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2, 3, 5])
async def test_concurrency_never_exceeds_limit(tmp_path, monkeypatch, limit):
    env = _make_env(tmp_path, max_concurrent=limit)

    started: list[str] = []
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        started.append(task.id)
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)

    tasks = [await _submit(env, f"t{i}.zip") for i in range(limit + 2)]

    for _ in range(50):
        await asyncio.sleep(0)
        if len(started) == limit:
            break
    assert len(started) == limit
    assert env.controller.active_count == limit
    assert env.controller.available_slots == 0

    for task in tasks[limit:]:
        assert task.status is TaskStatus.QUEUED


# ── Dynamic increase: admits queued tasks ─────────────────────────────────────

@pytest.mark.asyncio
async def test_dynamic_increase_admits_one_queued(env1, monkeypatch):
    started: list[str] = []
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        if task.name == "first.zip":
            await first_release.wait()
        elif task.name == "second.zip":
            await second_release.wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "first.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    second = await _submit(env1, "second.zip")
    assert second.status is TaskStatus.QUEUED
    assert env1.controller.active_count == 1

    env1.controller.set_max_concurrent(2)
    for _ in range(50):
        await asyncio.sleep(0)
        if env1.controller.active_count == 2:
            break
    assert env1.controller.active_count == 2
    await _wait_for_status(second, TaskStatus.DOWNLOADING)

    assert second.status is TaskStatus.DOWNLOADING
    first_release.set()
    second_release.set()
    await _wait_for_all((first, second))


@pytest.mark.asyncio
async def test_dynamic_increase_admits_multiple_queued(env1, monkeypatch):
    started: list[str] = []
    first_release = asyncio.Event()

    async def execute(task):
        started.append(task.id)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        if task.name == "first.zip":
            await first_release.wait()
        else:
            await asyncio.sleep(0)
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "first.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    second = await _submit(env1, "second.zip")
    third = await _submit(env1, "third.zip")
    fourth = await _submit(env1, "fourth.zip")
    assert env1.controller.queued_count == 3

    env1.controller.set_max_concurrent(4)
    await asyncio.sleep(0)

    assert env1.controller.active_count == 4
    assert env1.controller.available_slots == 0

    first_release.set()
    await _wait_for_all((first, second, third, fourth))


# ── Dynamic decrease: active tasks not cancelled ──────────────────────────────

@pytest.mark.asyncio
async def test_decrease_does_not_cancel_active(env3, monkeypatch):
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        env3.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env3.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env3.manager, "_execute_download", execute)

    tasks = [await _submit(env3, f"t{i}.zip") for i in range(3)]
    await _wait_for_status(tasks[0], TaskStatus.DOWNLOADING)
    assert env3.controller.active_count == 3

    env3.controller.set_max_concurrent(1)
    await asyncio.sleep(0)

    assert env3.controller.active_count == 3
    assert env3.controller.available_slots == 0

    tasks.append(await _submit(env3, "new.zip"))
    assert tasks[3].status is TaskStatus.QUEUED

    for tid in (tasks[0].id, tasks[1].id, tasks[2].id):
        holds[tid].set()
    await _wait_for_all(tasks[:3])


# ── Recovery after decrease ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_after_decrease(env3, monkeypatch):
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        env3.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env3.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env3.manager, "_execute_download", execute)

    tasks = [await _submit(env3, f"t{i}.zip") for i in range(3)]
    for _ in range(50):
        await asyncio.sleep(0)
        if len(holds) == 3:
            break
    assert env3.controller.active_count == 3

    env3.controller.set_max_concurrent(1)
    for _ in range(50):
        await asyncio.sleep(0)
    assert env3.controller.active_count == 3

    fourth = await _submit(env3, "t3.zip")
    assert fourth.status is TaskStatus.QUEUED

    holds[tasks[0].id].set()
    await _wait_for_status(tasks[0], TaskStatus.COMPLETED)
    for _ in range(50):
        await asyncio.sleep(0)
        if env3.controller.active_count == 2:
            break
    assert env3.controller.active_count == 2

    env3.controller.set_max_concurrent(3)
    for _ in range(50):
        await asyncio.sleep(0)
        if env3.controller.active_count == 3:
            break
    assert env3.controller.active_count == 3

    await _wait_for_status(fourth, TaskStatus.DOWNLOADING)
    holds[tasks[1].id].set()
    holds[tasks[2].id].set()
    holds[fourth.id].set()
    await _wait_for_all((tasks[1], tasks[2], fourth))


# ── Queue order preserved through concurrency increase ────────────────────────

@pytest.mark.asyncio
async def test_queue_order_preserved_through_concurrency_increase(env1, monkeypatch):
    started_names: list[str] = []
    first_release = asyncio.Event()

    async def execute(task):
        started_names.append(task.name)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await first_release.wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "first.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    second = await _submit(env1, "second.zip")
    third = await _submit(env1, "third.zip")
    fourth = await _submit(env1, "fourth.zip")
    assert env1.controller.queued_count == 3

    env1.controller.set_max_concurrent(3)
    await asyncio.sleep(0)

    assert env1.controller.active_count == 3

    async def execute_release(task):
        started_names.append(task.name)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await asyncio.sleep(0)
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute_release)
    first_release.set()
    await _wait_for_all((first, second, third, fourth))

    assert started_names.index("second.zip") < started_names.index("third.zip")
    assert started_names.index("second.zip") < started_names.index("fourth.zip")


# ── Pause frees slot, allows concurrency admission ────────────────────────────

@pytest.mark.asyncio
async def test_pause_frees_slot_allows_queued_admission(env, monkeypatch):
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        env.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env.manager, "_execute_download", execute)

    first = await _submit(env, "first.zip")
    second = await _submit(env, "second.zip")
    third = await _submit(env, "third.zip")

    await _wait_for_status(first, TaskStatus.DOWNLOADING)
    await _wait_for_status(second, TaskStatus.DOWNLOADING)
    assert third.status is TaskStatus.QUEUED
    assert env.controller.active_count == 2

    env.service.pause_task(first.id)
    await _wait_for_status(first, TaskStatus.PAUSED)
    for _ in range(50):
        await asyncio.sleep(0)
        if third.status is TaskStatus.DOWNLOADING:
            break
    assert third.status is TaskStatus.DOWNLOADING
    assert env.controller.active_count == 2

    holds[second.id].set()
    holds[third.id].set()
    await _wait_for_status(second, TaskStatus.COMPLETED)
    await _wait_for_status(third, TaskStatus.COMPLETED)


# ── Queued cancellation allows next task ──────────────────────────────────────

@pytest.mark.asyncio
async def test_queued_cancellation_admits_next(env1, monkeypatch):
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "first.zip")
    second = await _submit(env1, "second.zip")
    third = await _submit(env1, "third.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    assert second.status is TaskStatus.QUEUED
    assert third.status is TaskStatus.QUEUED

    env1.service.cancel_task(second.id)
    assert second.status is TaskStatus.CANCELLED

    holds[first.id].set()
    await _wait_for_status(first, TaskStatus.COMPLETED)
    for _ in range(50):
        await asyncio.sleep(0)
        if third.status is TaskStatus.DOWNLOADING:
            break
    assert third.status is TaskStatus.DOWNLOADING

    holds[third.id].set()
    await _wait_for_status(third, TaskStatus.COMPLETED)


# ── Active cancellation frees slot ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_active_cancellation_frees_slot(env1, monkeypatch):
    async def execute(task):
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await asyncio.Event().wait()

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "first.zip")
    second = await _submit(env1, "second.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)
    assert second.status is TaskStatus.QUEUED

    env1.service.cancel_task(first.id)
    await _wait_for_status(first, TaskStatus.CANCELLED)

    async def execute_second(task):
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await asyncio.sleep(0)
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute_second)

    await _wait_for_status(second, TaskStatus.COMPLETED)
    assert env1.controller.active_count == 0


# ── Failure releases slot ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_failure_releases_slot_allows_next(env1, monkeypatch):
    started: list[str] = []

    async def execute(task):
        started.append(task.id)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        if task.name == "fail.zip":
            raise RuntimeError("controlled failure")
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    failed = await _submit(env1, "fail.zip")
    next_task = await _submit(env1, "next.zip")
    await _wait_for_status(failed, TaskStatus.FAILED)
    await _wait_for_status(next_task, TaskStatus.COMPLETED)

    assert started == [failed.id, next_task.id]
    assert env1.controller.active_count == 0


# ── Retry obeys concurrency ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_obeys_concurrency_after_cancel(env1, monkeypatch):
    async def execute_fail(task):
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        raise RuntimeError("controlled failure")

    monkeypatch.setattr(env1.manager, "_execute_download", execute_fail)

    first = await _submit(env1, "first.zip")
    await _wait_for_status(first, TaskStatus.FAILED)
    assert first.status is TaskStatus.FAILED

    retry_release = asyncio.Event()

    async def execute_retry(task):
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await retry_release.wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute_retry)

    env1.service.retry_task(first.id)
    await _wait_for_status(first, TaskStatus.DOWNLOADING)
    assert env1.controller.active_count == 1
    assert env1.controller.available_slots == 0

    second = await _submit(env1, "second.zip")
    assert second.status is TaskStatus.QUEUED

    retry_release.set()
    await _wait_for_status(first, TaskStatus.COMPLETED)
    await _wait_for_status(second, TaskStatus.COMPLETED)
    assert env1.controller.active_count == 0


# ── Exactly-once under concurrency change ──────────────────────────────────────

@pytest.mark.asyncio
async def test_exactly_once_under_concurrency_increase(env1, monkeypatch):
    executions: list[str] = []
    release = asyncio.Event()

    async def execute(task):
        executions.append(task.id)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await release.wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "once.zip")
    await _wait_for_status(first, TaskStatus.DOWNLOADING)

    env1.controller.set_max_concurrent(3)
    await asyncio.sleep(0)

    duplicate = await _submit(env1, "once.zip")
    assert duplicate is first
    assert executions == [first.id]

    release.set()
    await _wait_for_status(first, TaskStatus.COMPLETED)
    assert executions == [first.id]


# ── Bookkeeping after concurrency transitions ───────────────────────────────────

@pytest.mark.asyncio
async def test_bookkeeping_after_concurrency_decrease_increase(env3, monkeypatch):
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        env3.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env3.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env3.manager, "_execute_download", execute)

    tasks = [await _submit(env3, f"t{i}.zip") for i in range(3)]
    await _wait_for_status(tasks[0], TaskStatus.DOWNLOADING)

    env3.controller.set_max_concurrent(1)
    await asyncio.sleep(0)
    assert env3.controller.active_count == 3

    env3.controller.set_max_concurrent(2)
    await asyncio.sleep(0)
    assert env3.controller.active_count == 3
    assert env3.controller.available_slots == 0

    for tid in (tasks[0].id, tasks[1].id, tasks[2].id):
        holds[tid].set()
    await _wait_for_all(tasks)

    assert env3.controller.active_count == 0
    assert env3.controller._scheduler._active_tasks == set()
    assert env3.controller._scheduler._scheduled_tasks == set()


# ── Repeated same-value concurrency set ───────────────────────────────────────

@pytest.mark.asyncio
async def test_repeated_set_max_concurrent_same_value(env1, monkeypatch):
    async def execute(task):
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        await asyncio.sleep(0)
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    first = await _submit(env1, "a.zip")
    env1.controller.set_max_concurrent(1)
    env1.controller.set_max_concurrent(1)
    await _wait_for_status(first, TaskStatus.COMPLETED)

    assert env1.controller.active_count == 0
    assert env1.controller.max_concurrent == 1


# ── All queued tasks complete after concurrency increase ───────────────────────

@pytest.mark.asyncio
async def test_all_queued_complete_after_increase(env1, monkeypatch):
    started: list[str] = []
    holds: dict[str, asyncio.Event] = {}

    async def execute(task):
        started.append(task.id)
        env1.manager._set_status(task, TaskStatus.DOWNLOADING)
        holds[task.id] = asyncio.Event()
        await holds[task.id].wait()
        env1.manager._set_status(task, TaskStatus.COMPLETED)

    monkeypatch.setattr(env1.manager, "_execute_download", execute)

    tasks = [await _submit(env1, f"q{i}.zip") for i in range(5)]

    for _ in range(50):
        await asyncio.sleep(0)
        if len(started) == 1:
            break
    assert len(started) == 1
    assert env1.controller.queued_count == 4

    env1.controller.set_max_concurrent(5)
    for _ in range(50):
        await asyncio.sleep(0)
        if len(holds) == 5:
            break
    assert env1.controller.active_count == 5
    assert env1.controller.queued_count == 0

    for tid in holds:
        holds[tid].set()
    await _wait_for_all(tasks)
    assert env1.controller.active_count == 0
    assert env1.controller.queued_count == 0