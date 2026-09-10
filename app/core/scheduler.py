"""V1.11 Scheduler — explicit queue scheduling for DownloadManager.

The Scheduler decides which queued downloads get the next available slot.
It reads the concurrency limit from DownloadManager and manages the
waiting/active task promotion lifecycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Optional

from app.core.downloader import DownloadManager
from app.core.task_manager import DownloadTask, TaskStatus
from app.utils.logger import get_logger

log = get_logger("vanta.core.scheduler")

TaskStatusCallback = Callable[[DownloadTask, TaskStatus], None]


class DownloadScheduler:
    """Scheduler that controls which queued tasks are allowed to start.

    Responsibilities:
    * Track queued tasks in FIFO order
    * Track active (running) tasks
    * Enforce max_concurrent limit from DownloadManager
    * Start next task when a slot becomes available
    * Prevent duplicate starts
    * React to concurrency limit changes
    """

    def __init__(
        self,
        manager: DownloadManager,
        on_status_change: Optional[TaskStatusCallback] = None,
    ):
        self._manager = manager
        self._on_status_change = on_status_change
        self._active_tasks: set[str] = set()
        self._scheduled_tasks: set[str] = set()
        self._running = False

    @property
    def max_concurrent(self) -> int:
        return self._manager.max_concurrent

    @property
    def active_count(self) -> int:
        return len(self._active_tasks)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent - self.active_count)

    @property
    def queued_tasks(self) -> list[DownloadTask]:
        """Return queued tasks in FIFO order (by queue_order)."""
        queued = [
            t
            for t in self._manager.download_tasks
            if t.status == TaskStatus.QUEUED
        ]
        return sorted(queued, key=lambda t: (t.queue_order, t.created_at))

    def start(self):
        """Begin scheduling. Call after all initial tasks are registered."""
        if self._running:
            return
        self._running = True
        self._sync_active_tasks()
        self._schedule_pending()

    def stop(self):
        """Stop scheduling. Active tasks continue running."""
        self._running = False

    def _sync_active_tasks(self):
        """Discover already-running tasks from the manager."""
        self._active_tasks.clear()
        for task in self._manager.download_tasks:
            if task.is_active:
                self._active_tasks.add(task.id)
                self._scheduled_tasks.add(task.id)

    def _schedule_pending(self):
        """Start queued tasks up to available slots."""
        if not self._running:
            return

        while self.available_slots > 0:
            next_task = self._get_next_queued_task()
            if next_task is None:
                break
            self._start_task(next_task)

    def _get_next_queued_task(self) -> Optional[DownloadTask]:
        """Get the next queued task that hasn't been scheduled yet."""
        for task in self.queued_tasks:
            if task.id not in self._scheduled_tasks:
                return task
        return None

    def _start_task(self, task: DownloadTask):
        """Delegate task start to DownloadManager."""
        if task.id in self._scheduled_tasks:
            return
        if task.id in self._active_tasks:
            return

        self._scheduled_tasks.add(task.id)
        self._active_tasks.add(task.id)

        if self._on_status_change:
            self._on_status_change(task, TaskStatus.DOWNLOADING)

        # Fire and forget - manager handles the actual execution
        asyncio.create_task(self._run_task(task))

    async def _run_task(self, task: DownloadTask):
        """Run the download task and handle completion."""
        try:
            await self._manager.start_download(task)
        except Exception as e:
            log.error("Scheduler task %s failed: %s", task.id, e)
        finally:
            self._on_task_finished(task)

    def _on_task_finished(self, task: DownloadTask):
        """Called when a task finishes (completed, failed, cancelled)."""
        self._active_tasks.discard(task.id)
        if self._running:
            self._schedule_pending()

    def on_task_status_changed(self, task: DownloadTask, old_status: TaskStatus):
        """Handle status changes from DownloadManager."""
        if not self._running:
            return

        # Task became active (e.g., was paused and resumed)
        if task.is_active and task.id not in self._active_tasks:
            self._active_tasks.add(task.id)
            self._scheduled_tasks.add(task.id)

        # Task left active state (completed, failed, cancelled, paused)
        was_active = task.id in self._active_tasks
        is_now_active = task.is_active

        if was_active and not is_now_active:
            self._active_tasks.discard(task.id)
            # Also remove from scheduled if terminal
            if task.is_terminal:
                self._scheduled_tasks.discard(task.id)
            if self._running:
                self._schedule_pending()

        # Task was removed from queue entirely
        if task.status == TaskStatus.CANCELLED and task.id in self._scheduled_tasks:
            self._scheduled_tasks.discard(task.id)

    def on_concurrency_changed(self):
        """Called when max_concurrent changes. Re-evaluate scheduling."""
        if not self._running:
            return
        self._schedule_pending()

    def on_task_added(self, task: DownloadTask):
        """Called when a new task is added to the queue."""
        if not self._running:
            return
        self._schedule_pending()

    def on_task_removed(self, task_id: str):
        """Called when a task is removed from the queue."""
        self._active_tasks.discard(task_id)
        self._scheduled_tasks.discard(task_id)
        if self._running:
            self._schedule_pending()

    def on_all_paused(self):
        """Called when pause_all() is invoked."""
        self._active_tasks.clear()
        self._scheduled_tasks.clear()