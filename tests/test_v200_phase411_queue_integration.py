"""V2.0 Phase 4.1.1 — Reviewed Download -> Queue Integration.

Proves that downloads confirmed by the Phase 3.7/3.8 review workflows enter the
existing queue **exactly once**, in **deterministic order**, preserving the
reviewed filename and destination — without redesigning the frozen scheduler /
downloader / task-manager architecture.

Integration boundary under test:
    DownloadService.start_file_download  ->  QueueController.add_download
        -> DownloadManager.register_task + DownloadScheduler.on_task_added

These are the only call sites; the workflow service only updates in-memory
duplicate-detection state (register_selected) and never touches the queue.

All queue-path assertions run with max_concurrent=0 so accepted tasks remain
QUEUED deterministically: no real network, no production Downloads folder, and no
timing-sensitive sleeps.
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
from app.services.download_workflow import DownloadWorkflowService


def _file(name="movie.zip", url="https://example.com/movie.zip", size=1024,
          content_type="application/zip"):
    return DownloadFile(name=name, url=url, size=size, content_type=content_type)


class _Env:
    def __init__(self, manager, controller, service, dest_dir):
        self.manager = manager
        self.controller = controller
        self.service = service
        self.dest_dir = dest_dir

    @property
    def tasks(self):
        return self.controller.tasks


@pytest.fixture
def env(tmp_path):
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()
    # max_concurrent=0 => the scheduler never promotes tasks to active, so they
    # stay QUEUED for the duration of the assertion with zero network access.
    manager = DownloadManager(max_concurrent=0, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None,
        download_manager=manager,
        queue_controller=controller,
        file_manager=FileManager(dest_dir),
    )
    return _Env(manager, controller, service, dest_dir)


def _start(env, **kwargs):
    """Synchronously run start_file_download to completion on a fresh loop."""
    return asyncio.run(env.service.start_file_download(**kwargs))


# ── Single download: reviewed values enter the queue ──────────────────────────

def test_single_reviewed_download_reaches_queue(env):
    f = _file()
    task = _start(env, source_url="https://example.com/page", file=f,
                  destination=str(env.dest_dir), filename="government-report-2026.pdf")
    assert task is not None
    assert task in env.tasks
    assert env.controller.find_task(task.id) is task
    assert env.controller.queued_count == 1
    assert task.status is TaskStatus.QUEUED


def test_single_reviewed_filename_preserved(env):
    f = _file(name="movie.zip", url="https://example.com/movie.zip")
    task = _start(env, source_url="https://example.com/page", file=f,
                  destination=str(env.dest_dir), filename="government-report-2026.pdf")
    assert task is not None
    # Reviewed filename is used verbatim as the task name and the on-disk name.
    assert task.name == "government-report-2026.pdf"
    assert task.destination.endswith("government-report-2026.pdf")


def test_single_reviewed_destination_preserved(env):
    f = _file()
    task = _start(env, source_url="https://example.com/page", file=f,
                  destination=str(env.dest_dir), filename="report.pdf")
    assert task is not None
    # Reviewed destination is honored exactly (not silently replaced by default).
    assert str(env.dest_dir) in task.destination
    assert Path(task.destination).parent == env.dest_dir


# ── Bulk download: ordered, exact count ───────────────────────────────────────

def _bulk_files():
    return [
        _file(name="a.zip", url="https://example.com/a.zip"),
        _file(name="b.zip", url="https://example.com/b.zip"),
        _file(name="c.zip", url="https://example.com/c.zip"),
    ]


def test_bulk_reviewed_downloads_reach_queue(env):
    async def _run():
        tasks = []
        for f in _bulk_files():
            t = await env.service.start_file_download(
                source_url="https://example.com/page", file=f,
                destination=str(env.dest_dir), filename=f.name,
            )
            assert t is not None
            tasks.append(t)
        return tasks

    tasks = asyncio.run(_run())
    assert len(tasks) == 3
    assert all(t.status is TaskStatus.QUEUED for t in tasks)
    assert env.controller.queued_count == 3


def test_bulk_registration_order_is_deterministic(env):
    orders = []

    async def _run():
        for f in _bulk_files():
            t = await env.service.start_file_download(
                source_url="https://example.com/page", file=f,
                destination=str(env.dest_dir), filename=f.name,
            )
            orders.append(t.queue_order)

    asyncio.run(_run())
    # Deterministic FIFO assignment matching submission order A -> B -> C.
    assert orders == [0, 1, 2]
    queued = [t for t in env.tasks if t.status is TaskStatus.QUEUED]
    assert [t.queue_order for t in queued] == [0, 1, 2]
    assert [env.controller.get_queue_position(t.id) for t in queued] == [1, 2, 3]


def test_bulk_exact_entry_count(env):
    async def _run():
        for f in _bulk_files():
            await env.service.start_file_download(
                source_url="https://example.com/page", file=f,
                destination=str(env.dest_dir), filename=f.name,
            )

    asyncio.run(_run())
    assert len(env.tasks) == 3
    assert env.controller.queued_count == 3
    # No task was started (max_concurrent=0): nothing left the QUEUED state.
    assert env.controller.active_count == 0


# ── Selection / validation gates at the queue boundary ────────────────────────

def test_already_existing_file_not_queued(env):
    (env.dest_dir / "exists.zip").write_bytes(b"already here")
    f = _file(name="exists.zip", url="https://example.com/exists.zip")
    task = _start(env, source_url="https://example.com/page", file=f,
                  destination=str(env.dest_dir), filename="exists.zip")
    # The service boundary rejects an occupied destination; nothing is queued.
    assert task is None
    assert env.controller.queued_count == 0
    assert len(env.tasks) == 0


def test_duplicate_registration_produces_one_entry(env):
    """Same confirmed download (same download URL + reviewed destination)
    submitted twice must not create a second queue entry."""
    f = _file(name="movie.zip", url="https://example.com/movie.zip")
    first = _start(env, source_url="https://example.com/page", file=f,
                   destination=str(env.dest_dir), filename="movie.zip")
    second = _start(env, source_url="https://example.com/page", file=f,
                    destination=str(env.dest_dir), filename="movie.zip")
    assert first is not None
    # Returns the exact same task object — no duplicate registration.
    assert second is first
    assert env.controller.queued_count == 1
    assert len(env.tasks) == 1


def test_distinct_resources_produce_distinct_entries(env):
    """Two different resources (different URL) are both queued — the guard
    only collapses identical (url, destination) pairs."""
    f1 = _file(name="a.zip", url="https://example.com/a.zip")
    f2 = _file(name="b.zip", url="https://example.com/b.zip")
    t1 = _start(env, source_url="https://example.com/page", file=f1,
                destination=str(env.dest_dir), filename="a.zip")
    t2 = _start(env, source_url="https://example.com/page", file=f2,
                destination=str(env.dest_dir), filename="b.zip")
    assert t1 is not None and t2 is not None
    assert t1 is not t2
    assert env.controller.queued_count == 2


# ── Scheduler handoff (public API only) ───────────────────────────────────────

def test_queued_task_visible_to_scheduler(env):
    f = _file()
    task = _start(env, source_url="https://example.com/page", file=f,
                  destination=str(env.dest_dir), filename="report.pdf")
    # The existing scheduler's public `queued_tasks` property reflects the
    # freshly registered task, proving the handoff from the service boundary.
    assert task in env.controller._scheduler.queued_tasks
    # QueueController public API mirrors the scheduler's view.
    assert env.controller.queued_count == 1
    assert env.controller.get_queue_position(task.id) == 1
    assert env.controller.find_task(task.id) is task


# ── End-to-end: review selection gate + cancel gate at the boundary ───────────

def _make_view(name, url=None):
    from app.services.analysis_view import ResourceView
    return ResourceView(
        file=DownloadFile(name=name, url=url or f"https://example.com/{name}"),
    )


def _build_bulk_dialog(views, dest_dir):
    from app.ui.bulk_review import BulkReviewDialog
    fm = FileManager(dest_dir)
    return BulkReviewDialog(
        source_url="https://example.com/page",
        resource_views=views,
        file_manager=fm,
        download_dir=dest_dir,
        workflow=DownloadWorkflowService(fm),
    )


def test_reviewed_selection_enters_queue_only_ready(qapp, tmp_path):
    """Only explicitly accepted (checked) resources enter the queue.
    Already-existing files are auto-renamed and accepted under auto_rename policy."""
    from app.core.queue_controller import QueueController

    dest = tmp_path / "downloads"
    dest.mkdir()
    (dest / "exists.zip").write_bytes(b"existing")

    views = [_make_view("ready.zip"), _make_view("exists.zip")]
    dialog = _build_bulk_dialog(views, dest)
    # Uncheck "ready.zip" so it is excluded from confirmation.
    ready_entry, exists_entry = dialog._entries
    ready_entry.include_cb.setChecked(False)

    dialog._on_download()

    # Review layer: ready.zip was unchecked, exists.zip is auto-renamed to exists_1.zip and accepted
    assert len(dialog.accepted_entries) == 1
    assert dialog.accepted_entries[0][1] == "exists_1.zip"

    # Route the accepted set through the real queue service.
    manager = DownloadManager(max_concurrent=0, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None, download_manager=manager,
        queue_controller=controller, file_manager=FileManager(dest),
    )

    async def _route(accepted):
        for _entry, _fn, _d in accepted:
            await service.start_file_download(
                source_url="https://example.com/page",
                file=_entry.view.file, destination=str(_d), filename=_fn,
            )

    asyncio.run(_route(dialog.accepted_entries))

    assert controller.queued_count == 1
    assert len(controller.tasks) == 1


def test_ready_reviewed_resources_enter_queue_in_order(qapp, tmp_path):
    """Two checked, READY reviewed resources both reach the queue, preserving
    their reviewed filenames and deterministic FIFO order."""
    from app.core.queue_controller import QueueController

    dest = tmp_path / "downloads"
    dest.mkdir()

    views = [_make_view("ready_a.zip"), _make_view("ready_b.zip")]
    dialog = _build_bulk_dialog(views, dest)
    dialog._on_download()

    accepted = list(dialog.accepted_entries)
    assert len(accepted) == 2

    manager = DownloadManager(max_concurrent=0, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None, download_manager=manager,
        queue_controller=controller, file_manager=FileManager(dest),
    )

    async def _route():
        order = []
        for _entry, fn, d in accepted:
            t = await service.start_file_download(
                source_url="https://example.com/page",
                file=_entry.view.file, destination=str(d), filename=fn,
            )
            assert t is not None
            order.append(t)
        return order

    queued = asyncio.run(_route())
    assert len(queued) == 2
    assert [t.name for t in queued] == ["ready_a.zip", "ready_b.zip"]
    assert [t.queue_order for t in queued] == [0, 1]
    assert controller.get_queue_position(queued[0].id) == 1
    assert controller.get_queue_position(queued[1].id) == 2


def test_cancelled_review_enters_no_queue(qapp, tmp_path):
    """Cancelling the review dialog leaves the queue empty."""
    from app.core.queue_controller import QueueController

    dest = tmp_path / "downloads"
    dest.mkdir()
    manager = DownloadManager(max_concurrent=0, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None, download_manager=manager,
        queue_controller=controller, file_manager=FileManager(dest),
    )

    views = [_make_view("a.zip"), _make_view("b.zip")]
    dialog = _build_bulk_dialog(views, dest)
    # User cancels instead of confirming.
    dialog.reject()

    assert dialog.accepted_entries == []
    assert dialog.result() != 1

    # Driving the (empty) accepted set through the real service queues nothing.
    async def _route(accepted):
        for _entry, _fn, _d in accepted:
            await service.start_file_download(
                source_url="https://example.com/page",
                file=_entry.view.file, destination=str(_d), filename=_fn,
            )

    asyncio.run(_route(dialog.accepted_entries))
    assert controller.queued_count == 0
    assert len(controller.tasks) == 0


def test_cancelled_review_enters_no_queue(qapp, tmp_path):
    """Cancelling the review dialog leaves the queue empty."""
    from app.core.queue_controller import QueueController

    dest = tmp_path / "downloads"
    dest.mkdir()
    manager = DownloadManager(max_concurrent=0, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None, download_manager=manager,
        queue_controller=controller, file_manager=FileManager(dest),
    )

    views = [_make_view("a.zip"), _make_view("b.zip")]
    dialog = _build_bulk_dialog(views, dest)
    # User cancels instead of confirming.
    dialog.reject()

    assert dialog.accepted_entries == []
    assert dialog.result() != 1

    # Driving the (empty) accepted set through the real service queues nothing.
    async def _route(accepted):
        for _entry, _fn, _d in accepted:
            await service.start_file_download(
                source_url="https://example.com/page",
                file=_entry.view.file, destination=str(_d), filename=_fn,
            )

    asyncio.run(_route(dialog.accepted_entries))
    assert controller.queued_count == 0
    assert len(controller.tasks) == 0
