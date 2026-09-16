"""V2.0 Phase 4.1.2 — Queue Ordering & Deterministic Submission.

Verifies that confirmed downloads enter the existing queue in deterministic
order matching their submission / review order, that non-ready / excluded /
duplicate submissions do not consume queue positions, and that the existing
scheduler and retry/persistence contracts are preserved.

Implementation under test is **unchanged** production code:
    DownloadService.start_file_download  ->  QueueController.add_download
        -> DownloadManager.register_task + DownloadScheduler.on_task_added

`queue_order` is assigned by `QueueController._next_queue_order_value`
(monotonic max+1 at registration) and the scheduler consumes tasks sorted by
`(queue_order, created_at)`. These tests assert that contract end-to-end at the
service boundary.

Tests are network-free: they use max_concurrent=0 so accepted tasks remain
QUEUED (the scheduler never promotes them), which lets us assert ordering state
without real downloads or timing-sensitive sleeps. The existing
`test_v110_queue_controller.py` already covers live scheduler promotion and the
retry-queue-end behaviour; this module does not duplicate those and introduces
no new ordering algorithm.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadErrorType, DownloadTask, TaskStatus
from app.services.download_service import DownloadService
from app.services.download_workflow import DownloadWorkflowService


def _file(name="movie.zip", url=None, size=1024, content_type="application/zip"):
    return DownloadFile(name=name, url=url or f"https://example.com/{name}",
                        size=size, content_type=content_type)


class _QEnv:
    def __init__(self, manager, controller, service, dest_dir):
        self.manager = manager
        self.controller = controller
        self.service = service
        self.dest_dir = dest_dir

    @property
    def tasks(self):
        return self.controller.tasks


@pytest.fixture
def qenv(tmp_path):
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()
    # max_concurrent=0 => accepted tasks stay QUEUED; the scheduler never
    # promotes them, so assertions are deterministic & network-free.
    manager = DownloadManager(max_concurrent=0, allow_private_networks=True,
                              downloads_dir=tmp_path)
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None,
        download_manager=manager,
        queue_controller=controller,
        file_manager=FileManager(dest_dir),
    )
    return _QEnv(manager, controller, service, dest_dir)


def _start(qenv, filename, url=None, name=None):
    return asyncio.run(qenv.service.start_file_download(
        source_url="https://example.com/page",
        file=_file(name=name or filename, url=url or f"https://example.com/{filename}"),
        destination=str(qenv.dest_dir), filename=filename,
    ))


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


def _route_accepted(qenv, accepted):
    async def _run():
        order = []
        for _entry, fn, d in accepted:
            t = await qenv.service.start_file_download(
                source_url="https://example.com/page",
                file=_entry.view.file, destination=str(d), filename=fn,
            )
            order.append(t)
        return order
    return asyncio.run(_run())


def _queued_three(qenv):
    a = _start(qenv, "a.zip", url="https://example.com/a.zip")
    b = _start(qenv, "b.zip", url="https://example.com/b.zip")
    c = _start(qenv, "c.zip", url="https://example.com/c.zip")
    return a, b, c


# ── 1. Sequential submission ordering ──────────────────────────────────────────

def test_sequential_submissions_receive_increasing_queue_order(qenv):
    t_a = _start(qenv, "a.zip")
    t_b = _start(qenv, "b.zip", url="https://example.com/b.zip")
    t_c = _start(qenv, "c.zip", url="https://example.com/c.zip")
    assert t_a.queue_order < t_b.queue_order < t_c.queue_order


def test_queue_positions_are_sequential(qenv):
    t_a = _start(qenv, "a.zip")
    t_b = _start(qenv, "b.zip", url="https://example.com/b.zip")
    t_c = _start(qenv, "c.zip", url="https://example.com/c.zip")
    assert qenv.controller.get_queue_position(t_a.id) == 1
    assert qenv.controller.get_queue_position(t_b.id) == 2
    assert qenv.controller.get_queue_position(t_c.id) == 3


def test_submission_order_preserved_not_re_sorted(qenv):
    """The queue must not reorder by filename / URL / category / size — it
    keeps the submission order (here deliberately non-alphabetical)."""
    names = ["c.zip", "a.zip", "b.zip"]
    tasks = [_start(qenv, n, url=f"https://example.com/{n}") for n in names]
    assert [t.name for t in tasks] == names          # submission order, not sorted
    assert [t.queue_order for t in tasks] == [0, 1, 2]


# ── 2. Bulk review ordering ───────────────────────────────────────────────────

def test_bulk_review_accepted_order_preserved(qapp, qenv):
    views = [_make_view("z.zip"), _make_view("m.zip"), _make_view("a.zip")]
    dialog = _build_bulk_dialog(views, qenv.dest_dir)
    dialog._on_download()
    assert len(dialog.accepted_entries) == 3
    tasks = _route_accepted(qenv, dialog.accepted_entries)
    assert [t.name for t in tasks] == ["z.zip", "m.zip", "a.zip"]
    assert [t.queue_order for t in tasks] == [0, 1, 2]


def test_bulk_excluded_entry_does_not_consume_position(qapp, qenv):
    views = [_make_view("a.zip"), _make_view("b.zip"), _make_view("c.zip")]
    dialog = _build_bulk_dialog(views, qenv.dest_dir)
    dialog._entries[1].include_cb.setChecked(False)   # b excluded
    dialog._on_download()
    accepted = dialog.accepted_entries
    assert [fn for (_e, fn, _d) in accepted] == ["a.zip", "c.zip"]
    tasks = _route_accepted(qenv, accepted)
    assert [t.name for t in tasks] == ["a.zip", "c.zip"]
    assert [t.queue_order for t in tasks] == [0, 1]


def test_bulk_already_existing_excluded_from_queue(qapp, qenv):
    """An already-existing reviewed resource is excluded by the review layer and
    therefore never consumes a queue position."""
    (qenv.dest_dir / "exists.zip").write_bytes(b"existing")
    views = [_make_view("ready1.zip"), _make_view("exists.zip"), _make_view("ready2.zip")]
    dialog = _build_bulk_dialog(views, qenv.dest_dir)
    dialog._on_download()
    accepted = dialog.accepted_entries
    assert "exists.zip" not in [fn for (_e, fn, _d) in accepted]
    assert [fn for (_e, fn, _d) in accepted] == ["ready1.zip", "ready2.zip"]
    tasks = _route_accepted(qenv, accepted)
    assert [t.name for t in tasks] == ["ready1.zip", "ready2.zip"]
    assert qenv.controller.queued_count == 2


def test_bulk_filename_conflict_excluded_from_queue(qapp, qenv):
    """A filename collision between two reviewed resources excludes both from
    the accepted set, so neither consumes a queue slot."""
    views = [_make_view("a.zip"), _make_view("b.zip")]
    dialog = _build_bulk_dialog(views, qenv.dest_dir)
    dialog._entries[0].filename_edit.setText("same.zip")
    dialog._entries[1].filename_edit.setText("same.zip")
    dialog._recalc_all()
    dialog._on_download()
    assert dialog.accepted_entries == []
    _route_accepted(qenv, dialog.accepted_entries)
    assert qenv.controller.queued_count == 0
    assert len(qenv.controller.tasks) == 0


# ── 3. Exactly-once guard interaction with ordering ───────────────────────────

def test_duplicate_submission_returns_existing_task(qenv):
    first = _start(qenv, "dup.zip", url="https://example.com/dup.zip")
    second = _start(qenv, "dup.zip", url="https://example.com/dup.zip")
    assert second is first
    assert len(qenv.controller.tasks) == 1


def test_duplicate_submission_no_new_queue_position(qenv):
    a = _start(qenv, "a.zip", url="https://example.com/a.zip")
    b = _start(qenv, "b.zip", url="https://example.com/b.zip")
    a_again = _start(qenv, "a.zip", url="https://example.com/a.zip")
    c = _start(qenv, "c.zip", url="https://example.com/c.zip")
    assert a_again is a                       # no second A in the queue
    assert c.queue_order == b.queue_order + 1
    assert qenv.controller.queued_count == 3


# ── 6. Same URL, different destination ────────────────────────────────────────

def test_same_url_different_destination_independent(qenv, tmp_path):
    d1 = qenv.dest_dir / "a"; d1.mkdir()
    d2 = tmp_path / "b"; d2.mkdir()
    url = "https://example.com/file.zip"

    async def _run():
        t1 = await qenv.service.start_file_download(
            source_url="https://example.com/page",
            file=_file(name="file.zip", url=url),
            destination=str(d1), filename="file.zip",
        )
        t2 = await qenv.service.start_file_download(
            source_url="https://example.com/page",
            file=_file(name="file.zip", url=url),
            destination=str(d2), filename="file.zip",
        )
        return t1, t2

    t1, t2 = asyncio.run(_run())
    assert t1 is not None and t2 is not None
    assert t1 is not t2
    assert t1.destination != t2.destination
    assert t1.download_url == t2.download_url == url
    assert qenv.controller.queued_count == 2
    assert qenv.controller.get_queue_position(t1.id) == 1
    assert qenv.controller.get_queue_position(t2.id) == 2


# ── 7. Manual reordering ──────────────────────────────────────────────────────

def test_move_up_swaps_with_predecessor(qenv):
    a, b, c = _queued_three(qenv)
    assert qenv.controller.move_task_up(c.id) is True
    assert qenv.controller.get_queue_position(c.id) == 2
    assert qenv.controller.get_queue_position(b.id) == 3


def test_move_down_swaps_with_successor(qenv):
    a, b, c = _queued_three(qenv)
    assert qenv.controller.move_task_down(a.id) is True
    assert qenv.controller.get_queue_position(a.id) == 2
    assert qenv.controller.get_queue_position(b.id) == 1


def test_move_top_places_at_head(qenv):
    a, b, c = _queued_three(qenv)
    assert qenv.controller.move_task_to_top(c.id) is True
    assert [qenv.controller.get_queue_position(t.id) for t in (c, a, b)] == [1, 2, 3]


def test_move_bottom_places_at_tail(qenv):
    a, b, c = _queued_three(qenv)
    assert qenv.controller.move_task_to_bottom(a.id) is True
    assert [qenv.controller.get_queue_position(t.id) for t in (b, c, a)] == [1, 2, 3]


def test_manual_reorder_not_overwritten_by_auto_resort(qenv):
    """A user reorder must not be clobbered by a later automatic sort pass."""
    a, b, c = _queued_three(qenv)
    assert qenv.controller.move_task_to_top(c.id) is True
    ordered = [t.name for t in qenv.controller._scheduler.queued_tasks]
    assert ordered == ["c.zip", "a.zip", "b.zip"]


# ── 8. Scheduler ordering contract ─────────────────────────────────────────────

def test_scheduler_consumes_in_queue_order(qenv):
    a = _start(qenv, "a.zip", url="https://example.com/a.zip")
    b = _start(qenv, "b.zip", url="https://example.com/b.zip")
    c = _start(qenv, "c.zip", url="https://example.com/c.zip")

    sched = qenv.controller._scheduler
    # The scheduler's public view of the queue is ordered by queue_order.
    assert [t.id for t in sched.queued_tasks] == [a.id, b.id, c.id]
    # Selection contract: lowest queue_order first, then next, skipping
    # already-scheduled items — without introducing gaps or reordering.
    assert sched._get_next_queued_task() is a
    sched._scheduled_tasks.add(a.id)
    assert sched._get_next_queued_task() is b
    sched._scheduled_tasks.add(b.id)
    assert sched._get_next_queued_task() is c


# ── 9. Terminal-task ordering stability ───────────────────────────────────────

def test_completion_does_not_corrupt_remaining_order(qenv):
    a, b, c = _queued_three(qenv)
    a.status = TaskStatus.COMPLETED
    remaining = [t for t in qenv.tasks if t.status == TaskStatus.QUEUED]
    assert [t.name for t in remaining] == ["b.zip", "c.zip"]
    assert [t.queue_order for t in remaining] == sorted(t.queue_order for t in remaining)


def test_failure_does_not_corrupt_remaining_order(qenv):
    a, b, c = _queued_three(qenv)
    b.status = TaskStatus.FAILED
    remaining = [t for t in qenv.tasks if t.status == TaskStatus.QUEUED]
    assert [t.name for t in remaining] == ["a.zip", "c.zip"]
    assert [t.queue_order for t in remaining] == sorted(t.queue_order for t in remaining)


def test_cancellation_does_not_corrupt_remaining_order(qenv):
    a, b, c = _queued_three(qenv)
    qenv.controller.cancel_download(b)
    queued = [t for t in qenv.tasks if t.status == TaskStatus.QUEUED]
    assert [t.name for t in queued] == ["a.zip", "c.zip"]
    assert [qenv.controller.get_queue_position(t.id) for t in queued] == [1, 2]


# ── 10. Retry ordering ────────────────────────────────────────────────────────

def test_retry_does_not_create_duplicate(qenv):
    """Retry reuses the existing task id (no duplicate) and follows the
    existing queue contract (appended to the end)."""
    a, b, c = _queued_three(qenv)

    async def _retry():
        qenv.controller.retry_download(b.id)
        pending = qenv.manager._tasks.get(b.id)
        if pending and not pending.done():
            pending.cancel()

    b.error_type = DownloadErrorType.NETWORK
    b.status = TaskStatus.FAILED
    asyncio.run(_retry())

    assert b.status is TaskStatus.QUEUED
    assert b.queue_order == c.queue_order + 1      # moved to end of queue
    assert len(qenv.controller.tasks) == 3        # no duplicate created
    queued = sorted(
        (t for t in qenv.tasks if t.status == TaskStatus.QUEUED),
        key=lambda t: t.queue_order,
    )
    assert [t.id for t in queued] == [a.id, c.id, b.id]


# ── 11. Persistence / restoration ordering ────────────────────────────────────

def test_restored_queued_tasks_preserve_relative_order(tmp_path):
    from app.database.repositories import load_download_tasks, save_download_task

    a = DownloadTask(id="restore_a", name="a.zip",
                     source_url="https://example.com/a.zip",
                     download_url="https://example.com/a.zip",
                     destination=str(tmp_path / "a.zip"),
                     status=TaskStatus.QUEUED, queue_order=0)
    b = DownloadTask(id="restore_b", name="b.zip",
                     source_url="https://example.com/b.zip",
                     download_url="https://example.com/b.zip",
                     destination=str(tmp_path / "b.zip"),
                     status=TaskStatus.QUEUED, queue_order=1)
    c = DownloadTask(id="restore_c", name="c.zip",
                     source_url="https://example.com/c.zip",
                     download_url="https://example.com/c.zip",
                     destination=str(tmp_path / "c.zip"),
                     status=TaskStatus.QUEUED, queue_order=2)
    for task in (a, b, c):
        save_download_task(task)

    try:
        restored = load_download_tasks()
        rm = DownloadManager(max_concurrent=0, allow_private_networks=True,
                             downloads_dir=tmp_path)
        rm.restore_tasks(restored)
        mine = [t for t in rm.download_tasks if t.id in ("restore_a", "restore_b", "restore_c")]
        assert [t.status for t in mine] == [TaskStatus.QUEUED, TaskStatus.QUEUED, TaskStatus.QUEUED]
        # Restoted tasks retain their persisted queue_order, so their relative
        # order is deterministic regardless of other records in the DB.
        assert [t.queue_order for t in sorted(mine, key=lambda t: t.queue_order)] == [0, 1, 2]
        assert [t.id for t in sorted(mine, key=lambda t: t.queue_order)] == [a.id, b.id, c.id]
    finally:
        from app.database.connection import get_session
        from app.database.models import DownloadRecord
        db = get_session()
        try:
            db.query(DownloadRecord).filter(
                DownloadRecord.id.in_(["restore_a", "restore_b", "restore_c"])
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


# ── 12. Async (sequential) submission determinism ──────────────────────────────

def test_sequential_async_submissions_retain_order(qapp, qenv):
    """Mirrors MainWindow._start_reviewed_bulk_downloads: an awaited loop over
    accepted entries produces queue order matching iteration order."""
    views = [_make_view("a.zip"), _make_view("b.zip"), _make_view("c.zip")]
    dialog = _build_bulk_dialog(views, qenv.dest_dir)
    dialog._on_download()
    tasks = _route_accepted(qenv, dialog.accepted_entries)
    assert [t.queue_order for t in tasks] == [0, 1, 2]


# ── 13. Cancelled review produces no queue entries ─────────────────────────────

def test_cancelled_review_enters_no_queue(qapp, qenv):
    views = [_make_view("a.zip"), _make_view("b.zip")]
    dialog = _build_bulk_dialog(views, qenv.dest_dir)
    dialog.reject()
    assert dialog.accepted_entries == []
    _route_accepted(qenv, dialog.accepted_entries)
    assert qenv.controller.queued_count == 0
    assert len(qenv.controller.tasks) == 0
