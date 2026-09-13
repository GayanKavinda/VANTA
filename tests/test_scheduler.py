"""V1.11 tests — DownloadScheduler."""

from __future__ import annotations

import asyncio
import pytest

from app.core.downloader import DownloadManager
from app.core.queue_controller import QueueController
from app.core.scheduler import DownloadScheduler
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
def scheduler(manager):
    # Mock start_download to prevent actual downloads during scheduling tests
    # Use a never-completing wait to keep tasks "running"
    original_start = manager.start_download
    
    async def mock_start(task):
        task.status = TaskStatus.DOWNLOADING
        # Wait forever to simulate a running download
        await asyncio.Event().wait()
    
    manager.start_download = mock_start
    sched = DownloadScheduler(manager)
    yield sched
    manager.start_download = original_start


# ── Scheduler basic properties ──────────────────────────────────────────

def test_scheduler_initial_state(scheduler):
    assert scheduler.active_count == 0
    assert scheduler.available_slots == 3
    assert scheduler.max_concurrent == 3


def test_scheduler_uses_manager_limit(manager):
    manager.set_max_concurrent(5)
    scheduler = DownloadScheduler(manager)
    assert scheduler.max_concurrent == 5
    assert scheduler.available_slots == 5


def test_scheduler_does_not_own_concurrency(manager):
    """Scheduler reads limit from manager, doesn't have its own setting."""
    scheduler = DownloadScheduler(manager)
    assert not hasattr(scheduler, "_max_concurrent")


# ── FIFO ordering ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_queued_task(scheduler, manager):
    task_a = _make_task("a", TaskStatus.QUEUED)
    task_b = _make_task("b", TaskStatus.QUEUED)
    manager._download_tasks.extend([task_a, task_b])

    scheduler.start()

    # Both tasks should be started (max_concurrent=3, 0 active)
    assert task_a.id in scheduler._scheduled_tasks
    assert task_a.id in scheduler._active_tasks
    assert task_b.id in scheduler._scheduled_tasks
    assert task_b.id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_fifo_order(scheduler, manager):
    task_a = _make_task("a", TaskStatus.QUEUED)
    task_b = _make_task("b", TaskStatus.QUEUED)
    task_c = _make_task("c", TaskStatus.QUEUED)
    manager._download_tasks.extend([task_a, task_b, task_c])

    scheduler.start()

    # With max_concurrent=3, all three should start
    assert scheduler.active_count == 3
    assert {task_a.id, task_b.id, task_c.id} == scheduler._active_tasks


@pytest.mark.asyncio
async def test_active_tasks_are_synced(scheduler, manager):
    """Active tasks from manager should be synced to scheduler."""
    active = _make_task("active", TaskStatus.DOWNLOADING)
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.extend([active, queued])

    scheduler.start()

    # Active task should be synced
    assert active.id in scheduler._active_tasks
    # With max_concurrent=3 and 1 active, queued should also be scheduled
    assert queued.id in scheduler._scheduled_tasks
    assert queued.id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_terminal_tasks_are_skipped(scheduler, manager):
    completed = _make_task("done", TaskStatus.COMPLETED)
    failed = _make_task("fail", TaskStatus.FAILED)
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.extend([completed, failed, queued])

    scheduler.start()

    assert completed.id not in scheduler._scheduled_tasks
    assert failed.id not in scheduler._scheduled_tasks
    assert queued.id in scheduler._scheduled_tasks


# ── Slots ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_zero_available_slots(scheduler, manager):
    # Add 3 active tasks (at max_concurrent=3)
    for i in range(3):
        task = _make_task(f"t{i}", TaskStatus.DOWNLOADING)
        manager._download_tasks.append(task)

    scheduler.start()
    assert scheduler.available_slots == 0
    assert scheduler.active_count == 3


@pytest.mark.asyncio
async def test_one_available_slot(scheduler, manager):
    # Add 2 active tasks
    for i in range(2):
        task = _make_task(f"t{i}", TaskStatus.DOWNLOADING)
        manager._download_tasks.append(task)

    scheduler.start()
    assert scheduler.available_slots == 1
    assert scheduler.active_count == 2

    # Now add queued and schedule
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.append(queued)
    scheduler._schedule_pending()

    # Slot should be filled
    assert scheduler.active_count == 3
    assert scheduler.available_slots == 0
    assert queued.id in scheduler._scheduled_tasks


@pytest.mark.asyncio
async def test_two_available_slots(scheduler, manager):
    # Add 1 active task
    task = _make_task("active", TaskStatus.DOWNLOADING)
    manager._download_tasks.append(task)

    scheduler.start()
    assert scheduler.available_slots == 2
    assert scheduler.active_count == 1

    # Add 2 queued
    for i in range(2):
        queued = _make_task(f"q{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(queued)
    scheduler._schedule_pending()

    # Should have 3 active total (1 existing + 2 queued)
    assert scheduler.active_count == 3
    assert scheduler.available_slots == 0


@pytest.mark.asyncio
async def test_never_exceeds_limit(scheduler, manager):
    # Add 5 queued tasks but limit is 3
    for i in range(5):
        manager._download_tasks.append(_make_task(f"t{i}", TaskStatus.QUEUED))

    scheduler.start()

    assert scheduler.active_count == 3
    assert scheduler.available_slots == 0


# ── Promotion on completion ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_completion_promotes_next(scheduler, manager):
    # Add 4 queued tasks
    for i in range(2):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)  # Let tasks start

    # Verify 3 tasks were scheduled (max_concurrent=3)
    assert scheduler.active_count == 3
    assert len(scheduler._scheduled_tasks) == 3

    # Simulate one active task completing
    completed_task_id = next(iter(scheduler._active_tasks))
    task_obj = manager.find_task(completed_task_id)
    task_obj.status = TaskStatus.COMPLETED
    scheduler.on_task_status_changed(task_obj, TaskStatus.DOWNLOADING)

    # Wait for promotion
    await asyncio.sleep(0.05)

    # Next queued task should be promoted
    assert scheduler.active_count == 3  # 2 remaining + 1 promoted
    assert manager.find_task("queued1").id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_failure_promotes_next(scheduler, manager):
    # Add 4 tasks to test promotion (max=3, so 3 start, 1 queued)
    active1 = _make_task("active1", TaskStatus.QUEUED)
    active2 = _make_task("active2", TaskStatus.QUEUED)
    active3 = _make_task("active3", TaskStatus.QUEUED)
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.extend([active1, active2, active3, queued])

    scheduler.start()
    await asyncio.sleep(0.05)
    # 3 start, 1 queued
    assert scheduler.active_count == 3
    assert queued.id not in scheduler._active_tasks

    # Simulate failure of one active
    active1.status = TaskStatus.FAILED
    scheduler.on_task_status_changed(active1, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Queued task should be promoted
    assert scheduler.active_count == 3
    assert queued.id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_cancellation_promotes_next(scheduler, manager):
    # Add 4 tasks to test promotion (max=3, so 3 start, 1 queued)
    active1 = _make_task("active1", TaskStatus.QUEUED)
    active2 = _make_task("active2", TaskStatus.QUEUED)
    active3 = _make_task("active3", TaskStatus.QUEUED)
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.extend([active1, active2, active3, queued])

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3
    assert queued.id not in scheduler._active_tasks

    # Simulate cancellation
    active1.status = TaskStatus.CANCELLED
    scheduler.on_task_status_changed(active1, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Queued task should be promoted
    assert scheduler.active_count == 3
    assert queued.id in scheduler._active_tasks


# ── Dynamic concurrency ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_increase_concurrency(scheduler, manager):
    # 3 active, limit=3
    for i in range(3):
        manager._download_tasks.append(_make_task(f"a{i}", TaskStatus.DOWNLOADING))
    for i in range(2):
        manager._download_tasks.append(_make_task(f"q{i}", TaskStatus.QUEUED))

    scheduler.start()
    assert scheduler.active_count == 3

    # Increase limit to 5
    manager.set_max_concurrent(5)
    scheduler.on_concurrency_changed()

    assert scheduler.active_count == 5  # Should start 2 more


def test_decrease_concurrency(scheduler, manager):
    # 5 active, limit=5
    manager.set_max_concurrent(5)
    for i in range(5):
        manager._download_tasks.append(_make_task(f"a{i}", TaskStatus.DOWNLOADING))

    scheduler.start()
    assert scheduler.active_count == 5

    # Decrease limit to 2
    manager.set_max_concurrent(2)
    scheduler.on_concurrency_changed()

    # Active tasks should NOT be cancelled, just no new ones started
    assert scheduler.active_count == 5
    assert scheduler.available_slots == 0


def test_active_downloads_not_cancelled_on_decrease(scheduler, manager):
    manager.set_max_concurrent(3)
    for i in range(3):
        manager._download_tasks.append(_make_task(f"a{i}", TaskStatus.DOWNLOADING))

    scheduler.start()

    manager.set_max_concurrent(1)
    scheduler.on_concurrency_changed()

    # All 3 should still be active
    assert scheduler.active_count == 3


# ── Duplicate protection ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_cannot_start_twice(scheduler, manager):
    task = _make_task("t1", TaskStatus.QUEUED)
    manager._download_tasks.append(task)

    scheduler.start()
    scheduler._start_task(task)

    # Try to start again
    scheduler._start_task(task)

    assert scheduler.active_count == 1


@pytest.mark.asyncio
async def test_concurrent_schedule_calls(scheduler, manager):
    for i in range(5):
        manager._download_tasks.append(_make_task(f"t{i}", TaskStatus.QUEUED))

    scheduler.start()
    scheduler._schedule_pending()
    scheduler._schedule_pending()  # Call twice

    assert scheduler.active_count == 3


# ── Empty queue ─────────────────────────────────────────────────────────

def test_empty_queue(scheduler):
    scheduler.start()
    assert scheduler.active_count == 0
    assert scheduler.available_slots == 3


# ── Task removal ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_removed_clears_tracking(scheduler, manager):
    # Mock start_download to prevent actual downloads
    original_start = manager.start_download
    
    async def mock_start(task):
        task.status = TaskStatus.DOWNLOADING
    
    manager.start_download = mock_start
    
    try:
        task = _make_task("t1", TaskStatus.QUEUED)
        manager._download_tasks.append(task)

        scheduler.start()
        await asyncio.sleep(0.05)
        # Task should be scheduled
        assert task.id in scheduler._scheduled_tasks

        scheduler.on_task_removed("t1")

        assert task.id not in scheduler._active_tasks
        assert task.id not in scheduler._scheduled_tasks
    finally:
        manager.start_download = original_start


def test_all_paused_clears_tracking(scheduler, manager):
    for i in range(3):
        manager._download_tasks.append(_make_task(f"a{i}", TaskStatus.DOWNLOADING))

    scheduler.start()
    assert scheduler.active_count == 3

    scheduler.on_all_paused()

    assert scheduler.active_count == 0
    assert len(scheduler._scheduled_tasks) == 0


# ── Integration with QueueController ────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_through_queue_controller(manager, tmp_path):
    controller = QueueController(manager, max_concurrent=2)

    # Add 3 tasks
    task_a = await controller.add_download("a", "http://a", "http://a", str(tmp_path / "a"))
    task_b = await controller.add_download("b", "http://b", "http://b", str(tmp_path / "b"))
    task_c = await controller.add_download("c", "http://c", "http://c", str(tmp_path / "c"))

    # Wait for scheduler to process
    await asyncio.sleep(0.1)

    # Only 2 should be active (max_concurrent=2)
    active = [t for t in manager.download_tasks if t.is_active]
    assert len(active) == 2
    assert controller.active_count == 2
    assert controller.available_slots == 0


# ── QueueController scheduler properties ────────────────────────────────

@pytest.fixture
def controller(manager):
    return QueueController(manager)


def test_queue_controller_scheduler_properties(controller, manager):
    controller._manager._download_tasks = [
        _make_task("a", TaskStatus.DOWNLOADING),
        _make_task("b", TaskStatus.QUEUED),
    ]
    controller._scheduler._sync_active_tasks()

    assert controller.active_count == 1
    assert controller.queued_count == 1
    assert controller.available_slots == 2  # max=3, active=1


# ── Lifecycle regression tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pause_resume_lifecycle(scheduler, manager):
    """Test DOWNLOADING -> PAUSED -> QUEUED -> DOWNLOADING lifecycle."""
    active = _make_task("active", TaskStatus.QUEUED)
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.extend([active, queued])

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 2

    # Pause the active task
    active.status = TaskStatus.PAUSED
    scheduler.on_task_status_changed(active, TaskStatus.DOWNLOADING)
    
    # Active task should be removed from both active and scheduled
    assert active.id not in scheduler._active_tasks
    assert active.id not in scheduler._scheduled_tasks
    assert scheduler.active_count == 1  # queued task promoted
    
    # Resume the paused task
    active.status = TaskStatus.QUEUED
    scheduler.on_task_status_changed(active, TaskStatus.PAUSED)
    
    # Resumed task should be queued and wait for slot
    assert active.id not in scheduler._active_tasks
    assert active.id not in scheduler._scheduled_tasks
    assert scheduler.active_count == 1  # queued still active, active now queued


@pytest.mark.asyncio
async def test_pause_all_resume_all_lifecycle(scheduler, manager):
    """Test pause_all -> resume_all properly reschedules."""
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3  # max=3
    assert scheduler.available_slots == 0

    # pause_all via controller (simulate)
    for task in manager.download_tasks:
        if task.is_active:
            task.status = TaskStatus.PAUSED
            scheduler.on_task_status_changed(task, TaskStatus.DOWNLOADING)
    
    scheduler.on_all_paused()
    assert scheduler.active_count == 0
    assert len(scheduler._scheduled_tasks) == 0

    # resume_all via controller (simulate)
    for task in manager.download_tasks:
        if task.status == TaskStatus.PAUSED:
            task.status = TaskStatus.QUEUED
            scheduler.on_task_status_changed(task, TaskStatus.PAUSED)
    
    scheduler.reschedule()
    await asyncio.sleep(0.05)
    
    # Should have 3 active again (max=3)
    assert scheduler.active_count == 3


@pytest.mark.asyncio
async def test_restore_tasks_schedules_queued(scheduler, manager):
    """Test that restore_tasks wakes scheduler for queued tasks."""
    # Add some tasks with QUEUED status (simulating restored tasks)
    for i in range(4):
        t = _make_task(f"restored{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    # Start scheduler (simulating app startup)
    scheduler.start()
    
    # Simulate controller calling on_task_added for each restored task
    for task in manager.download_tasks:
        if task.status == TaskStatus.QUEUED:
            scheduler.on_task_added(task)

    await asyncio.sleep(0.05)
    
    # Should have 3 active (max=3), 1 queued
    assert scheduler.active_count == 3
    assert scheduler.available_slots == 0


@pytest.mark.asyncio
async def test_completion_promotes_waiting_task(scheduler, manager):
    """Test that completing a task promotes the next queued task."""
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3

    # Complete one active task
    completed_id = next(iter(scheduler._active_tasks))
    completed_task = manager.find_task(completed_id)
    completed_task.status = TaskStatus.COMPLETED
    scheduler.on_task_status_changed(completed_task, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Should still have 3 active (2 remaining + 1 promoted)
    assert scheduler.active_count == 3
    # The first queued task should now be active
    assert manager.find_task("queued0").id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_failure_promotes_waiting_task(scheduler, manager):
    """Test that failing a task promotes the next queued task."""
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3

    # Fail one active task
    failed_id = next(iter(scheduler._active_tasks))
    failed_task = manager.find_task(failed_id)
    failed_task.status = TaskStatus.FAILED
    scheduler.on_task_status_changed(failed_task, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Should still have 3 active (2 remaining + 1 promoted)
    assert scheduler.active_count == 3
    assert manager.find_task("queued0").id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_cancellation_promotes_waiting_task(scheduler, manager):
    """Test that cancelling a task promotes the next queued task."""
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3

    # Cancel one active task
    cancelled_id = next(iter(scheduler._active_tasks))
    cancelled_task = manager.find_task(cancelled_id)
    cancelled_task.status = TaskStatus.CANCELLED
    scheduler.on_task_status_changed(cancelled_task, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Should still have 3 active (2 remaining + 1 promoted)
    assert scheduler.active_count == 3
    assert manager.find_task("queued0").id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_remove_queued_task_not_started(scheduler, manager):
    """Test that removing a queued task prevents it from being started later."""
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3

    # Remove a queued task from BOTH manager and scheduler (like controller does)
    manager.remove_task("queued0")
    scheduler.on_task_removed("queued0")

    # Complete an active task
    completed_id = next(iter(scheduler._active_tasks))
    completed_task = manager.find_task(completed_id)
    completed_task.status = TaskStatus.COMPLETED
    scheduler.on_task_status_changed(completed_task, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Should promote queued1, not queued0 (which was removed)
    assert scheduler.active_count == 3
    # queued0 was removed from manager, so find_task returns None
    assert manager.find_task("queued0") is None
    assert manager.find_task("queued1").id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_pause_active_does_not_block_scheduler(scheduler, manager):
    """Test that pausing an active task frees a slot for promotion."""
    # Add 4 tasks so there's one queued to promote
    active1 = _make_task("active1", TaskStatus.QUEUED)
    active2 = _make_task("active2", TaskStatus.QUEUED)
    active3 = _make_task("active3", TaskStatus.QUEUED)
    queued = _make_task("queued", TaskStatus.QUEUED)
    manager._download_tasks.extend([active1, active2, active3, queued])

    scheduler.start()
    await asyncio.sleep(0.05)
    assert scheduler.active_count == 3  # max=3, first 3 start

    # Pause one active
    active1.status = TaskStatus.PAUSED
    scheduler.on_task_status_changed(active1, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Queued should be promoted, active_count stays at 3
    assert scheduler.active_count == 3
    assert queued.id in scheduler._active_tasks
    assert active1.id not in scheduler._active_tasks
    assert active1.id not in scheduler._scheduled_tasks


# ── V1.12 Regression: Scheduler reorder edge cases ───────────────────────

@pytest.mark.asyncio
async def test_on_queue_reordered_does_not_re_select_active_task(scheduler, manager):
    """on_queue_reordered() must not cause already-admitted tasks to be re-selected."""
    # Add 3 queued tasks (max_concurrent=3, all will start)
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    # Add 2 more queued tasks
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)

    # All 3 should be active, 2 queued
    assert scheduler.active_count == 3
    assert len(scheduler._scheduled_tasks) == 3

    # Simulate reorder while admitted tasks still have QUEUED status
    # (they haven't transitioned to PREPARING/DOWNLOADING yet)
    scheduler.on_queue_reordered()

    # Should not re-select already active tasks
    assert scheduler.active_count == 3
    assert len(scheduler._scheduled_tasks) == 3  # unchanged

    # Call _schedule_pending directly to ensure no duplicate starts
    scheduler._schedule_pending()
    assert scheduler.active_count == 3
    assert len(scheduler._scheduled_tasks) == 3


@pytest.mark.asyncio
async def test_reorder_while_task_admitted_queued_status(scheduler, manager):
    """Reorder while an admitted task still shows QUEUED status should not create duplicates."""
    # Use max_concurrent=1 to control how many start
    manager.set_max_concurrent(1)
    
    active = _make_task("active", TaskStatus.QUEUED)
    queued1 = _make_task("queued1", TaskStatus.QUEUED)
    queued2 = _make_task("queued2", TaskStatus.QUEUED)
    manager._download_tasks.extend([active, queued1, queued2])

    scheduler.start()
    await asyncio.sleep(0.05)

    # Only 1 should be admitted (max_concurrent=1)
    assert active.id in scheduler._active_tasks
    assert active.id in scheduler._scheduled_tasks
    assert scheduler.active_count == 1

    # Reorder queue
    scheduler.on_queue_reordered()

    # Reorder again (simulating rapid UI reorder operations)
    scheduler.on_queue_reordered()
    scheduler.on_queue_reordered()

    # Should not have duplicate selections
    assert scheduler.active_count == 1
    assert active.id in scheduler._active_tasks
    assert len(scheduler._scheduled_tasks) == 1

    # Complete the active task, next should be promoted
    active.status = TaskStatus.COMPLETED
    scheduler.on_task_status_changed(active, TaskStatus.QUEUED)
    await asyncio.sleep(0.05)

    # queued1 should be promoted
    assert scheduler.active_count == 1
    assert queued1.id in scheduler._active_tasks


@pytest.mark.asyncio
async def test_scheduler_never_repeatedly_selects_active_task(scheduler, manager):
    """_get_next_queued_task should never return a task already in _active_tasks."""
    # Use max_concurrent=2 so not all start immediately
    manager.set_max_concurrent(2)
    
    for i in range(3):
        t = _make_task(f"t{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)

    # 2 admitted, 1 queued
    assert scheduler.active_count == 2
    active_ids = set(scheduler._active_tasks)

    # Manually call _get_next_queued_task - should return the queued task (not active ones)
    next_task = scheduler._get_next_queued_task()
    assert next_task is not None
    assert next_task.id not in active_ids

    # Call on_queue_reordered - should not make _get_next_queued_task return active tasks
    scheduler.on_queue_reordered()
    next_task = scheduler._get_next_queued_task()
    assert next_task is not None
    assert next_task.id not in active_ids

    # Complete one active task
    completed_id = next(iter(active_ids))
    completed_task = manager.find_task(completed_id)
    completed_task.status = TaskStatus.COMPLETED
    scheduler.on_task_status_changed(completed_task, TaskStatus.DOWNLOADING)
    await asyncio.sleep(0.05)

    # The completed task should be removed from active, next promoted
    assert completed_id not in scheduler._active_tasks
    assert scheduler.active_count == 2  # 1 remaining + 1 promoted


@pytest.mark.asyncio
async def test_completion_still_promotes_after_reorder(scheduler, manager):
    """Completion should still promote next queued task after reorder operations."""
    for i in range(3):
        t = _make_task(f"active{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)
    for i in range(2):
        t = _make_task(f"queued{i}", TaskStatus.QUEUED)
        manager._download_tasks.append(t)

    scheduler.start()
    await asyncio.sleep(0.05)

    # Reorder queue
    scheduler.on_queue_reordered()

    # Complete one active task
    completed_id = next(iter(scheduler._active_tasks))
    completed_task = manager.find_task(completed_id)
    completed_task.status = TaskStatus.COMPLETED
    scheduler.on_task_status_changed(completed_task, TaskStatus.DOWNLOADING)

    await asyncio.sleep(0.05)

    # Should promote a queued task
    assert scheduler.active_count == 3
    # At least one queued task should now be active
    queued_now_active = [t for t in manager._download_tasks if t.id in scheduler._active_tasks and t.id.startswith("queued")]
    assert len(queued_now_active) >= 1


@pytest.mark.asyncio
async def test_queue_controller_no_scheduler_private_access():
    """QueueController should not access scheduler._scheduled_tasks directly."""
    from app.core.queue_controller import QueueController
    from app.core.downloader import DownloadManager

    manager = DownloadManager(max_concurrent=3, allow_private_networks=True)
    controller = QueueController(manager)

    # Verify QueueController doesn't access _scheduled_tasks
    import inspect
    source = inspect.getsource(controller.retry_failed)
    assert "_scheduled_tasks" not in source, "QueueController should not access scheduler._scheduled_tasks directly"
    assert "is_task_scheduled" in source, "QueueController should use scheduler.is_task_scheduled()"