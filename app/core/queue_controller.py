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

    # Post-download
    if "verification" in msg or "checksum" in msg:
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
    """

    def __init__(
        self,
        manager: DownloadManager,
        max_concurrent: int | None = None,
    ):
        self._manager = manager
        self._max_concurrent = max_concurrent or manager.max_concurrent
        self._queue_callbacks: list[QueueChangeCallback] = []
        manager.add_progress_callback(self._on_manager_progress)

    # ── properties ────────────────────────────────────────────────────

    @property
    def tasks(self) -> list[DownloadTask]:
        return self._manager.download_tasks

    @property
    def download_manager(self) -> DownloadManager:
        return self._manager

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def active_count(self) -> int:
        return sum(
            1 for t in self._manager.download_tasks if t.is_active
        )

    @property
    def queued_count(self) -> int:
        return sum(
            1
            for t in self._manager.download_tasks
            if t.status == TaskStatus.QUEUED
        )

    @property
    def available_slots(self) -> int:
        return max(0, self._max_concurrent - self.active_count)

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
        task = await self._manager.add_download(
            name=name,
            source_url=source_url,
            download_url=download_url,
            destination=destination,
        )
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

        self._emit_queue_change(task)

    def resume_download(self, task: DownloadTask):
        if task.status not in (TaskStatus.PAUSED, TaskStatus.FAILED):
            return
        self._manager.resume_download(task)
        self._emit_queue_change(task)

    def cancel_download(self, task: DownloadTask):
        if task.is_terminal:
            return
        self._manager.cancel_download(task)
        self._emit_queue_change(task)

    def retry_download(self, task_id: str):
        self._manager.retry_task(task_id)
        task = self._manager.find_task(task_id)
        if task is not None:
            self._emit_queue_change(task)

    def retry_failed(self):
        self._manager.retry_failed()
        self._emit_queue_change(None)

    def pause_all(self):
        for task in list(self._manager.download_tasks):
            if task.status in (
                TaskStatus.QUEUED,
                TaskStatus.DOWNLOADING,
                TaskStatus.PREPARING,
                TaskStatus.VERIFYING,
            ):
                self.pause_download(task)
        self._emit_queue_change(None)

    def resume_all(self):
        for task in list(self._manager.download_tasks):
            if task.status == TaskStatus.PAUSED:
                self.resume_download(task)
        self._emit_queue_change(None)

    def cancel_all(self):
        for task in list(self._manager.download_tasks):
            if not task.is_terminal:
                self.cancel_download(task)
        self._emit_queue_change(None)

    def clear_completed(self) -> list[str]:
        removed_ids = self._manager.clear_completed()
        self._emit_queue_change(None)
        return removed_ids

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
            self._emit_queue_change(None)
        return removed

    # ── settings ──────────────────────────────────────────────────────

    def set_max_concurrent(self, value: int):
        self._max_concurrent = value
        self._manager.set_max_concurrent(value)
        self._emit_queue_change(None)

    def set_speed_limit(self, bytes_per_sec: int):
        self._manager.set_speed_limit(bytes_per_sec)

    # ── persistence restoration ───────────────────────────────────────

    def restore_tasks(
        self, tasks: list[DownloadTask]
    ) -> list[DownloadTask]:
        interrupted = self._manager.restore_tasks(tasks)
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

        self._emit_queue_change(task)
