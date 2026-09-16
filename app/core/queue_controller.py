"""V1.10 queue controller — explicit queue scheduling above DownloadManager.

Architecture:

    DownloadService
         ↓
    QueueController  ← this module
         ↓
    DownloadManager
         ↓
    Persistence / HTTP
"""

from __future__ import annotations

from typing import Callable, Optional

from app.core.downloader import DownloadManager
from app.core.scheduler import DownloadScheduler
from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.repositories import delete_download_task
from app.utils.logger import get_logger

log = get_logger("vanta.core.queue_controller")

QueueChangeCallback = Callable[[Optional[DownloadTask]], None]


def classify_download_error(task: DownloadTask) -> DownloadErrorType:
    """Reclassify a failed download task's error type by inspecting its message.

    The downloader sets ``error_type`` explicitly for some error paths
    (HTML response, unsafe redirect, range unsupported, disk failure,
    verification failure).  For all other exceptions it leaves
    ``error_type`` as ``UNKNOWN`` with the raw exception string in
    ``error``.

    This function bridges that gap: given a task whose ``error_type`` is
    still ``UNKNOWN``, it inspects ``task.error`` for characteristic
    keywords and returns a more specific type.

    Check order matters: more specific patterns are tested first so that
    e.g. an HTML-response message (which also contains "denied" and
    "blocked") is not misclassified as access-denied or unsafe-redirect.
    """
    if task.error_type != DownloadErrorType.UNKNOWN:
        return task.error_type

    msg = (task.error or "").lower()

    # HTTP responses
    if "html page" in msg or "text/html" in msg:
        return DownloadErrorType.HTML_RESPONSE

    if "text response" in msg:
        return DownloadErrorType.ACCESS_DENIED

    # Security / redirect
    if "unsafe" in msg:
        return DownloadErrorType.UNSAFE_REDIRECT

    if "redirect loop" in msg:
        return DownloadErrorType.REDIRECT_LOOP

    # HTTP protocol
    if "416" in msg or "not satisfiable" in msg or "range unsupported" in msg:
        return DownloadErrorType.RANGE_UNSUPPORTED

    # V1.13 — Network failure classifications (order matters: more specific first)
    # Server unavailable (5xx, connection refused, etc.) - check before timeout
    if (
        "503" in msg
        or "502" in msg
        or "504" in msg
        or "connection refused" in msg
        or "service unavailable" in msg
        or "bad gateway" in msg
        or "gateway timeout" in msg
    ):
        return DownloadErrorType.SERVER_UNAVAILABLE

    # Resource not found (404)
    if "404" in msg or "not found" in msg:
        return DownloadErrorType.RESOURCE_NOT_FOUND

    # Timeout
    if "timeout" in msg or "timed out" in msg:
        return DownloadErrorType.TIMEOUT

    # Connection interrupted
    if (
        "connection" in msg
        and ("reset" in msg or "aborted" in msg or "broken" in msg or "closed" in msg)
    ):
        return DownloadErrorType.CONNECTION_INTERRUPTED

    # Broken pipe (often appears without "connection")
    if "broken pipe" in msg:
        return DownloadErrorType.CONNECTION_INTERRUPTED

    # Post-download
    if "verification" in msg:
        return DownloadErrorType.VERIFICATION

    # V1.13 — Existing file conflict
    if "file exists" in msg or "already exists" in msg:
        return DownloadErrorType.EXISTING_FILE

    # V1.13 — Corrupt partial file (checksum mismatch specific to partial)
    if "corrupt" in msg or "mismatch" in msg or "hash" in msg:
        return DownloadErrorType.CORRUPT_PARTIAL

    # Checksum (after corrupt/mismatch checks)
    if "checksum" in msg:
        return DownloadErrorType.VERIFICATION

    # Filesystem
    if (
        "no space" in msg
        or "permission denied" in msg
        or "no such file" in msg
        or "file not found" in msg
        or "disk full" in msg
    ):
        return DownloadErrorType.DISK

    return DownloadErrorType.UNKNOWN


class QueueController:
    """Orchestration layer that manages the download queue state.

    The ``QueueController`` wraps a :class:`DownloadManager` and adds:

    * **Explicit pause for queued tasks** — fixes the bug where
      ``DownloadManager.pause_download`` silently no-ops on a ``QUEUED``
      task that has no live ``asyncio`` task.
    * **Error re-classification** — when a download fails with
      ``UNKNOWN`` error type, the controller inspects the error message
      and assigns a more specific type.
    * **Available-slot tracking** — exposes ``active_count`` and
      ``available_slots`` for UI scheduling decisions.
    * **Queue change callbacks** — UI components subscribe to be
      notified whenever a task's lifecycle state changes.
    * **Scheduler integration** — uses ``DownloadScheduler`` to control
      which queued tasks are promoted to active downloads.
    """

    def __init__(
        self,
        manager: DownloadManager,
        max_concurrent: int | None = None,
    ):
        self._manager = manager
        if max_concurrent is not None:
            manager.set_max_concurrent(max_concurrent)

        self._scheduler = DownloadScheduler(
            manager,
            on_status_change=self._on_scheduler_status_change,
        )
        self._queue_callbacks: list[QueueChangeCallback] = []
        manager.add_progress_callback(self._on_manager_progress)

        # Start the scheduler
        self._scheduler.start()

    def _next_queue_order_value(self) -> int:
        """Return the next queue_order value for a new queued task."""
        if not self._manager.download_tasks:
            return 0
        return max(task.queue_order for task in self._manager.download_tasks) + 1

    def _on_scheduler_status_change(self, task: DownloadTask, status: TaskStatus):
        """Called by scheduler when it admits a task for execution.
        
        The scheduler uses this to track its internal state. The actual
        status transitions (QUEUED -> PREPARING -> DOWNLOADING) are
        managed by DownloadManager.start_download() and _run().
        """
        # Scheduler internally tracks admission; don't override manager's status
        self._emit_queue_change(task)

    # ── properties ────────────────────────────────────────────────────

    @property
    def tasks(self) -> list[DownloadTask]:
        return self._manager.download_tasks

    @property
    def download_manager(self) -> DownloadManager:
        return self._manager

    @property
    def max_concurrent(self) -> int:
        return self._manager.max_concurrent

    @property
    def active_count(self) -> int:
        return self._scheduler.active_count

    @property
    def queued_count(self) -> int:
        return sum(
            1
            for t in self._manager.download_tasks
            if t.status == TaskStatus.QUEUED
        )

    @property
    def available_slots(self) -> int:
        return self._scheduler.available_slots

    @property
    def incomplete_tasks(self) -> list[DownloadTask]:
        return [
            t for t in self._manager.download_tasks if not t.is_terminal
        ]

    def find_task(self, task_id: str) -> DownloadTask | None:
        return self._manager.find_task(task_id)

    # ── callbacks ─────────────────────────────────────────────────────

    def add_queue_callback(self, callback: QueueChangeCallback):
        self._queue_callbacks.append(callback)

    def remove_queue_callback(self, callback: QueueChangeCallback):
        if callback in self._queue_callbacks:
            self._queue_callbacks.remove(callback)

    def _emit_queue_change(self, task: Optional[DownloadTask] = None):
        for cb in list(self._queue_callbacks):
            cb(task)

    # ── task operations ───────────────────────────────────────────────

    async def add_download(
        self,
        name: str,
        source_url: str,
        download_url: str,
        destination: str,
    ) -> DownloadTask:
        import uuid
        task = DownloadTask(
            id=uuid.uuid4().hex[:12],
            name=name,
            source_url=source_url,
            download_url=download_url,
            destination=destination,
        )
        # Assign queue_order before registering
        task.queue_order = self._next_queue_order_value()

        # Register the task without starting it - scheduler will handle promotion
        self._manager.register_task(task)
        self._scheduler.on_task_added(task)
        self._emit_queue_change(task)
        return task

    def pause_download(self, task: DownloadTask):
        if task.is_terminal:
            return

        if task.status == TaskStatus.PAUSED:
            return

        self._manager.pause_download(task)

        # Safety-net: if the underlying asyncio task was not found or
        # already completed, the manager's pause_download may have left
        # the status unchanged (still QUEUED).  Force it to PAUSED so
        # the UI and queue accounting are consistent.
        if task.status != TaskStatus.PAUSED:
            self._manager._set_status(
                task, TaskStatus.PAUSED, "Download was paused"
            )

        self._scheduler.on_task_status_changed(task, task.status)
        self._emit_queue_change(task)

    def resume_download(self, task: DownloadTask):
        if task.status not in (TaskStatus.PAUSED, TaskStatus.FAILED):
            return
        self._manager.resume_download(task)
        self._scheduler.on_task_status_changed(task, task.status)
        self._emit_queue_change(task)

    def cancel_download(self, task: DownloadTask):
        if task.is_terminal:
            return
        self._manager.cancel_download(task)
        self._scheduler.on_task_status_changed(task, task.status)
        self._scheduler.on_task_removed(task.id)
        self._emit_queue_change(task)

    def retry_download(self, task_id: str):
        # A failed task may still be finishing its scheduler wrapper when the
        # retry is requested. Clear the old admission before reusing its id so
        # the retry can be admitted exactly once.
        task = self._manager.find_task(task_id)
        if task is None or task.status != TaskStatus.FAILED:
            return
        self._scheduler.on_task_removed(task_id)
        # Reserve the id while retry reset emits QUEUED; otherwise the
        # scheduler can launch a second retry before the manager starts its
        # own retry task.
        self._scheduler.on_task_retried(task)
        self._manager.retry_task(task_id)
        task = self._manager.find_task(task_id)
        if task is not None:
            # Assign queue_order from monotonic counter (end of queue)
            task.queue_order = self._next_queue_order_value()
            self._emit_queue_change(task)

    def retry_failed(self):
        self._manager.retry_failed()
        # Scheduler will pick up new queued tasks via on_task_added in retry_download
        # but we need to manually check for any retried tasks
        for task in self._manager.download_tasks:
            if task.status == TaskStatus.QUEUED and not self._scheduler.is_task_scheduled(task.id):
                # Assign queue_order if not already set
                if task.queue_order == 0:
                    task.queue_order = self._next_queue_order_value()
                self._scheduler.on_task_added(task)
        self._emit_queue_change(None)
        self._scheduler.on_queue_reordered()

    def pause_all(self):
        for task in list(self._manager.download_tasks):
            if task.status in (
                TaskStatus.QUEUED,
                TaskStatus.DOWNLOADING,
                TaskStatus.PREPARING,
                TaskStatus.VERIFYING,
            ):
                self.pause_download(task)
        self._scheduler.on_all_paused()
        self._emit_queue_change(None)

    def resume_all(self):
        for task in list(self._manager.download_tasks):
            if task.status == TaskStatus.PAUSED:
                self.resume_download(task)
        self._scheduler.reschedule()
        self._emit_queue_change(None)

    def cancel_all(self):
        for task in list(self._manager.download_tasks):
            if not task.is_terminal:
                self.cancel_download(task)
        self._emit_queue_change(None)

    def clear_completed(self) -> list[str]:
        removed_ids = self._manager.clear_completed()
        for task_id in removed_ids:
            self._scheduler.on_task_removed(task_id)
        self._emit_queue_change(None)
        self._scheduler.on_queue_reordered()
        return removed_ids

    # ── queue reordering ────────────────────────────────────────────────

    def _get_queued_tasks_sorted(self) -> list[DownloadTask]:
        """Return queued tasks sorted by queue_order then created_at."""
        return sorted(
            [t for t in self._manager.download_tasks if t.status == TaskStatus.QUEUED],
            key=lambda t: (t.queue_order, t.created_at),
        )

    def _rebuild_queue_order(self):
        """Rebuild queue_order for all queued tasks to be sequential 0..N-1."""
        queued = self._get_queued_tasks_sorted()
        for idx, task in enumerate(queued):
            task.queue_order = idx

    def move_task_up(self, task_id: str) -> bool:
        """Move a queued task up one position in the queue."""
        queued = self._get_queued_tasks_sorted()
        idx = next((i for i, t in enumerate(queued) if t.id == task_id), -1)
        if idx <= 0:
            return False
        # Swap queue_order with previous task
        queued[idx].queue_order, queued[idx - 1].queue_order = (
            queued[idx - 1].queue_order,
            queued[idx].queue_order,
        )
        self._emit_queue_change(None)
        self._scheduler.on_queue_reordered()
        return True

    def move_task_down(self, task_id: str) -> bool:
        """Move a queued task down one position in the queue."""
        queued = self._get_queued_tasks_sorted()
        idx = next((i for i, t in enumerate(queued) if t.id == task_id), -1)
        if idx < 0 or idx >= len(queued) - 1:
            return False
        # Swap queue_order with next task
        queued[idx].queue_order, queued[idx + 1].queue_order = (
            queued[idx + 1].queue_order,
            queued[idx].queue_order,
        )
        self._emit_queue_change(None)
        self._scheduler.on_queue_reordered()
        return True

    def move_task_to_top(self, task_id: str) -> bool:
        """Move a queued task to the top of the queue."""
        queued = self._get_queued_tasks_sorted()
        idx = next((i for i, t in enumerate(queued) if t.id == task_id), -1)
        if idx <= 0:
            return False
        task = queued.pop(idx)
        queued.insert(0, task)
        for i, t in enumerate(queued):
            t.queue_order = i
        self._emit_queue_change(None)
        self._scheduler.on_queue_reordered()
        return True

    def move_task_to_bottom(self, task_id: str) -> bool:
        """Move a queued task to the bottom of the queue."""
        queued = self._get_queued_tasks_sorted()
        idx = next((i for i, t in enumerate(queued) if t.id == task_id), -1)
        if idx < 0 or idx >= len(queued) - 1:
            return False
        task = queued.pop(idx)
        queued.append(task)
        for i, t in enumerate(queued):
            t.queue_order = i
        self._emit_queue_change(None)
        self._scheduler.on_queue_reordered()
        return True

    def get_queue_position(self, task_id: str) -> int | None:
        """Get the 1-based queue position of a queued task, or None if not queued."""
        queued = self._get_queued_tasks_sorted()
        idx = next((i for i, t in enumerate(queued) if t.id == task_id), -1)
        return idx + 1 if idx >= 0 else None

    def remove_task(self, task_id: str) -> bool:
        """Permanently remove a task from both the runtime and the database.

        Returns ``True`` if a task was removed, ``False`` if not found.
        """
        removed = self._manager.remove_task(task_id)
        if removed:
            try:
                delete_download_task(task_id)
            except Exception as e:
                log.error("Failed to delete task %s from database: %s", task_id, e)
            self._scheduler.on_task_removed(task_id)
            # Rebuild queue order for remaining queued tasks
            self._rebuild_queue_order()
            self._emit_queue_change(None)
            self._scheduler.on_queue_reordered()
        return removed

    # ── settings ──────────────────────────────────────────────────────

    def set_max_concurrent(self, value: int):
        self._manager.set_max_concurrent(value)
        self._scheduler.on_concurrency_changed()
        self._emit_queue_change(None)

    def set_speed_limit(self, bytes_per_sec: int):
        self._manager.set_speed_limit(bytes_per_sec)

    # ── persistence restoration ───────────────────────────────────────

    def restore_tasks(
        self, tasks: list[DownloadTask]
    ) -> list[DownloadTask]:
        interrupted = self._manager.restore_tasks(tasks)
        # Wake scheduler for any restored queued tasks
        for task in self._manager.download_tasks:
            if task.status == TaskStatus.QUEUED and task.id not in self._scheduler._scheduled_tasks:
                self._scheduler.on_task_added(task)
        self._emit_queue_change(None)
        return interrupted

    # ── internal: progress listener ──────────────────────────────────

    def _on_manager_progress(self, task: DownloadTask):
        """Intercept manager progress updates for error classification."""
        if (
            task.status == TaskStatus.FAILED
            and task.error_type == DownloadErrorType.UNKNOWN
        ):
            task.error_type = classify_download_error(task)

        self._scheduler.on_task_status_changed(task, task.status)
        self._emit_queue_change(task)
