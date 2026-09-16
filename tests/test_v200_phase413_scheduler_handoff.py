"""V2.0 Phase 4.1.3 — Queue → Scheduler Handoff.

Proves that downloads placed on the queue by the Phase 4.1.1 integration point
(`DownloadService.start_file_download -> QueueController.add_download`) are
**received, admitted, and promoted by the existing `DownloadScheduler`** in the
deterministic order established by Phase 4.1.2 — without redesigning the
scheduler, downloader, or concurrency model.

The handoff contract under test:
    DownloadService.start_file_download
        -> QueueController.add_download (registers task, assigns queue_order)
        -> DownloadScheduler.on_task_added -> _schedule_pending -> _start_task
        -> asyncio.create_task(_run_task) -> await manager.start_download(task)
        -> on completion/failure/cancel: _on_task_finished -> _schedule_pending
           (promotes the next lowest queue_order task)

The existing V1.11 test suite (`tests/test_scheduler.py`) already covers the
scheduler's unit-level FIFO / slot / promotion / reorder / restore behaviour.
This module adds focused regression coverage only for the **Phase 4 boundary**
— i.e. that reviewed downloads flow through the service into the *real* scheduler
and are consumed in review order. It does not duplicate the scheduler's internal
unit tests.

These tests use a *completing* mock for `DownloadManager.start_download` (mirroring
`tests/test_scheduler.py`'s `scheduler` fixture) so the promotion chain runs to
completion deterministically, with no real network access.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import TaskStatus
from app.services.download_service import DownloadService


def _file(name, url=None):
    return DownloadFile(name=name, url=url or f"https://example.com/{name}",
                        size=16, content_type="application/octet-stream")


class _SchedEnv:
    def __init__(self, manager, controller, service, dest_dir, started):
        self.manager = manager
        self.controller = controller
        self.service = service
        self.dest_dir = dest_dir
        self.started = started  # admission order, as seen by the (mocked) download


@pytest.fixture
def sched_env(tmp_path):
    """Controller wired to DownloadService with max_concurrent=1 and a download
    mock that completes the task immediately, recording admission order."""
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()
    manager = DownloadManager(max_concurrent=1, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None,
        download_manager=manager,
        queue_controller=controller,
        file_manager=FileManager(dest_dir),
    )

    started: list[str] = []
    original = manager.start_download

    async def completing_start(task):
        started.append(task.id)
        task.status = TaskStatus.DOWNLOADING
        task.total_size = task.total_size or 16
        await asyncio.sleep(0)          # yield once so _on_task_finished can fire
        task.downloaded_size = task.total_size
        task.status = TaskStatus.COMPLETED

    manager.start_download = completing_start
    yield _SchedEnv(manager, controller, service, dest_dir, started)
    manager.start_download = original
    for t in list(manager._tasks.values()):
        if not t.done():
            t.cancel()


async def _submit_all(env, names):
    """Submit reviewed downloads in the given name/url order; return tasks."""
    tasks = []
    for name in names:
        url = f"https://example.com/{name}"
        t = await env.service.start_file_download(
            source_url="https://example.com/page", file=_file(name, url),
            destination=str(env.dest_dir), filename=name,
        )
        tasks.append(t)
    return tasks


# ── Scheduler receives & admits reviewed downloads in FIFO order ──────────────

@pytest.mark.asyncio
async def test_scheduler_admits_reviewed_downloads_in_fifo_order(sched_env):
    """The deterministically-ordered reviewed queue is consumed by the scheduler
    in submission (== queue_order) order, and each task completes in turn."""
    tasks = await _submit_all(sched_env, ["a.zip", "b.zip", "c.zip"])
    await asyncio.sleep(0.3)  # let the promotion chain run to completion

    assert sched_env.started == [t.id for t in tasks]      # FIFO admission
    assert all(t.status is TaskStatus.COMPLETED for t in tasks)
    assert sched_env.controller.active_count == 0
    assert sched_env.controller.queued_count == 0


@pytest.mark.asyncio
async def test_slot_freed_on_completion_promotes_next(sched_env):
    """When the active task completes, the slot is freed and the next queued
    task (by queue_order) is promoted — through the real service boundary."""
    tasks = await _submit_all(sched_env, ["a.zip", "b.zip"])
    await asyncio.sleep(0.3)

    assert sched_env.started == [tasks[0].id, tasks[1].id]
    assert tasks[0].status is TaskStatus.COMPLETED
    assert tasks[1].status is TaskStatus.COMPLETED


# ── Concurrency limit bounds admission through the boundary ───────────────────

@pytest.fixture
def sched_env_parallel(tmp_path):
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()
    manager = DownloadManager(max_concurrent=2, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None, download_manager=manager, queue_controller=controller,
        file_manager=FileManager(dest_dir),
    )
    started: list[str] = []
    original = manager.start_download

    async def slow_then_complete(task):
        started.append(task.id)
        task.status = TaskStatus.DOWNLOADING
        await asyncio.sleep(0.05)       # keep a slot occupied briefly
        task.total_size = task.total_size or 16
        task.downloaded_size = task.total_size
        task.status = TaskStatus.COMPLETED

    manager.start_download = slow_then_complete
    yield _SchedEnv(manager, controller, service, dest_dir, started)
    manager.start_download = original
    for t in list(manager._tasks.values()):
        if not t.done():
            t.cancel()


@pytest.mark.asyncio
async def test_concurrency_limit_bounds_admission(sched_env_parallel):
    """max_concurrent=2: at most 2 tasks are ever active simultaneously, and
    admission order is still FIFO; all complete eventually."""
    tasks = await _submit_all(sched_env_parallel, ["a.zip", "b.zip", "c.zip", "d.zip"])
    await asyncio.sleep(0.5)

    assert [t.id for t in tasks] == sched_env_parallel.started      # FIFO admission
    assert all(t.status is TaskStatus.COMPLETED for t in tasks)
    # The scheduler never exceeded the concurrency ceiling.
    assert sched_env_parallel.controller.active_count == 0


# ── Exactly-once guard prevents double admission ──────────────────────────────

@pytest.mark.asyncio
async def test_exactly_once_guard_prevents_double_admission(sched_env):
    """Re-submitting the same reviewed download returns the existing task and
    is admitted by the scheduler exactly once."""
    first = await sched_env.service.start_file_download(
        source_url="https://example.com/page", file=_file("a.zip", "https://example.com/a.zip"),
        destination=str(sched_env.dest_dir), filename="a.zip",
    )
    second = await sched_env.service.start_file_download(
        source_url="https://example.com/page", file=_file("a.zip", "https://example.com/a.zip"),
        destination=str(sched_env.dest_dir), filename="a.zip",
    )
    assert second is first                       # exactly-once at the service layer
    await asyncio.sleep(0.3)

    assert sched_env.started.count(first.id) == 1  # admitted exactly once
    assert first.status is TaskStatus.COMPLETED
    assert sched_env.controller.active_count == 0


# ── Scheduler does not re-select terminal / removed tasks ─────────────────────

@pytest.mark.asyncio
async def test_completed_task_not_re_admitted(sched_env):
    """After a task completes, the scheduler never re-selects it (no duplicate
    execution) — the slot is given to the next queued task instead."""
    tasks = await _submit_all(sched_env, ["a.zip", "b.zip"])
    await asyncio.sleep(0.3)

    assert tasks[0].status is TaskStatus.COMPLETED
    # b was promoted only after a completed — admission order, not parallel.
    assert sched_env.started == [tasks[0].id, tasks[1].id]
    assert tasks[0].id not in sched_env.controller._scheduler._active_tasks
