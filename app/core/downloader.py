import asyncio
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.utils.constants import CHUNK_SIZE
from app.utils.logger import get_logger

log = get_logger("vanta.core.downloader")

ProgressCallback = Callable[[DownloadTask], None]

_DOWNLOAD_TIMEOUT = httpx.Timeout(
    connect=15.0,
    read=60.0,
    write=60.0,
    pool=30.0,
)
_FLUSH_INTERVAL = 5.0


class DownloadManager:

    def __init__(self, max_concurrent: int = 3):
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, asyncio.Task] = {}
        self._download_tasks: list[DownloadTask] = []
        self._callbacks: list[ProgressCallback] = []
        self._speed_limit_bytes_per_sec: int = 0

    @property
    def download_tasks(self) -> list[DownloadTask]:
        return self._download_tasks

    def add_progress_callback(self, callback: ProgressCallback):
        self._callbacks.append(callback)

    def remove_progress_callback(self, callback: ProgressCallback):
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _emit_progress(self, task: DownloadTask):
        for cb in self._callbacks:
            cb(task)

    def _set_status(
        self,
        task: DownloadTask,
        status: TaskStatus,
        error: Optional[str] = None,
    ):
        task.status = status
        task.error = error
        task.updated_at = time.time()
        changed_queue_tasks = self._update_queue_positions()
        self._emit_progress(task)
        for t in changed_queue_tasks:
            if t.id != task.id:
                self._emit_progress(t)

    def _update_queue_positions(self):
        changed = []
        queued = [
            t for t in self._download_tasks
            if t.status == TaskStatus.QUEUED
        ]
        for idx, task in enumerate(queued, start=1):
            if task.queue_position != idx or task.queue_order != idx:
                task.queue_position = idx
                task.queue_order = idx
                changed.append(task)
        for task in self._download_tasks:
            if task.status != TaskStatus.QUEUED and task.queue_position != 0:
                task.queue_position = 0
                changed.append(task)
        return changed

    def get_queue_position(self, task_id: str) -> int:
        task = self.find_task(task_id)
        if task is None or task.status != TaskStatus.QUEUED:
            return 0
        queued = [
            t for t in self._download_tasks
            if t.status == TaskStatus.QUEUED
        ]
        for idx, t in enumerate(queued, start=1):
            if t.id == task_id:
                return idx
        return 0

    def find_task(self, task_id: str) -> DownloadTask | None:
        for task in self._download_tasks:
            if task.id == task_id:
                return task
        return None

    async def add_download(
        self,
        name: str,
        source_url: str,
        download_url: str,
        destination: str,
        existing_task: Optional[DownloadTask] = None,
    ) -> DownloadTask:
        if existing_task:
            task = existing_task
            task.status = TaskStatus.QUEUED
            task.error = None
        else:
            import uuid
            task = DownloadTask(
                id=uuid.uuid4().hex[:12],
                name=name,
                source_url=source_url,
                download_url=download_url,
                destination=destination,
            )

        if task in self._download_tasks:
            idx = self._download_tasks.index(task)
            self._download_tasks[idx] = task
        else:
            self._download_tasks.append(task)

        changed = self._update_queue_positions()

        self._emit_progress(task)

        for t in changed:
            if t.id != task.id:
                self._emit_progress(t)

        asyncio_task = asyncio.create_task(self._run(task))
        self._tasks[task.id] = asyncio_task

        return task

    async def _run(self, task: DownloadTask):
        async with self._semaphore:
            was_paused = False
            try:
                await self._execute_download(task)
            except asyncio.CancelledError:
                if task.status == TaskStatus.PAUSED:
                    was_paused = True
                else:
                    self._set_status(
                        task,
                        TaskStatus.CANCELLED,
                        "Download was cancelled",
                    )
                raise
            except Exception as e:
                log.error("Download failed for '%s': %s", task.name, e, exc_info=True)
                if task.error_type == DownloadErrorType.UNKNOWN:
                    task.error_type = DownloadErrorType.UNKNOWN
                self._set_status(task, TaskStatus.FAILED, str(e))
            finally:
                self._tasks.pop(task.id, None)
                if not was_paused:
                    self._emit_progress(task)

    async def _execute_download(self, task: DownloadTask):
        self._set_status(task, TaskStatus.PREPARING)

        dest_path = Path(task.destination)
        part_path = dest_path.with_suffix(dest_path.suffix + ".part")

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        resume_position = 0
        if part_path.exists():
            resume_position = part_path.stat().st_size
            if resume_position > 0:
                log.info("Resuming download from %d bytes", resume_position)

        async with httpx.AsyncClient(follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT) as client:
            headers = {}
            if resume_position > 0:
                headers["Range"] = f"bytes={resume_position}-"

            need_restart = False
            effective_resume = resume_position

            async with client.stream(
                "GET",
                task.download_url,
                headers=headers,
            ) as response:
                if response.status_code == 416:
                    need_restart = True
                else:
                    if response.status_code == 206:
                        task.supports_resume = True
                    elif response.status_code == 200 and resume_position > 0:
                        task.error_type = DownloadErrorType.RANGE_UNSUPPORTED
                        task.supports_resume = False
                        log.warning(
                            "Server does not support Range requests (returned 200). "
                            "Restarting download from zero."
                        )
                        part_path.unlink(missing_ok=True)
                        effective_resume = 0
                    elif response.status_code == 200:
                        task.supports_resume = False

                    if response.status_code not in (200, 206):
                        response.raise_for_status()

                    self._validate_response(response, task)
                    await self._stream_to_file(response, task, part_path, effective_resume)

            if need_restart:
                part_path.unlink(missing_ok=True)
                headers.pop("Range", None)

                async with client.stream(
                    "GET",
                    task.download_url,
                    headers=headers,
                ) as r2:
                    self._validate_response(r2, task)
                    if r2.status_code not in (200, 206):
                        r2.raise_for_status()
                    await self._stream_to_file(r2, task, part_path, 0)

        self._set_status(task, TaskStatus.VERIFYING)

        if self._verify_file(part_path, dest_path, task):
            part_path.rename(dest_path)
            self._set_status(task, TaskStatus.COMPLETED)
        else:
            task.error_type = DownloadErrorType.VERIFICATION
            self._set_status(task, TaskStatus.FAILED, "File verification failed")
            part_path.unlink(missing_ok=True)

    def _validate_response(self, response: httpx.Response, task: DownloadTask):
        content_type = response.headers.get("content-type", "").lower()

        if "text/html" in content_type:
            task.error_type = DownloadErrorType.HTML_RESPONSE
            raise ValueError(
                f"Server returned an HTML page instead of '{task.name}' "
                f"(Content-Type: {content_type}). "
                "This usually means access was denied or blocked."
            )

        if "text/plain" in content_type and response.headers.get("content-disposition") is None:
            task.error_type = DownloadErrorType.ACCESS_DENIED
            raise ValueError(
                f"Server returned a text response instead of '{task.name}' "
                f"(Content-Type: {content_type})."
            )

    async def _stream_to_file(
        self,
        response: httpx.Response,
        task: DownloadTask,
        part_path: Path,
        resume_position: int,
    ):
        total_size = int(response.headers.get("content-length", 0))

        if resume_position > 0 and total_size > 0:
            total_size += resume_position

        if total_size > 0:
            task.total_size = total_size

        self._set_status(task, TaskStatus.DOWNLOADING)

        downloaded = resume_position
        session_downloaded = 0
        last_emit = downloaded
        start_time = time.monotonic()
        last_flush = start_time
        throttle_start = start_time
        throttle_bytes = 0

        file_mode = "ab" if resume_position > 0 else "wb"

        try:
            with open(part_path, file_mode) as f:
                try:
                    async for chunk in response.aiter_bytes(CHUNK_SIZE):
                        f.write(chunk)

                        session_downloaded += len(chunk)
                        downloaded += len(chunk)
                        throttle_bytes += len(chunk)
                        elapsed = time.monotonic() - start_time
                        speed = session_downloaded / elapsed if elapsed > 0 else 0.0

                        task.downloaded_size = downloaded
                        if total_size > 0:
                            task.progress = round((downloaded / total_size) * 100, 1)
                        task.speed = speed
                        task.updated_at = time.time()

                        if downloaded - last_emit >= CHUNK_SIZE:
                            self._emit_progress(task)
                            last_emit = downloaded

                        if self._speed_limit_bytes_per_sec > 0:
                            throttle_elapsed = time.monotonic() - throttle_start
                            expected_time = throttle_bytes / self._speed_limit_bytes_per_sec
                            if throttle_elapsed < expected_time:
                                await asyncio.sleep(expected_time - throttle_elapsed)
                            throttle_start = time.monotonic()
                            throttle_bytes = 0

                        now = time.monotonic()
                        if now - last_flush >= _FLUSH_INTERVAL:
                            f.flush()
                            last_flush = now
                finally:
                    f.flush()
        except OSError:
            task.error_type = DownloadErrorType.DISK
            raise

        self._emit_progress(task)

    def _verify_file(self, part_path: Path, dest_path: Path, task: DownloadTask) -> bool:
        if not part_path.exists():
            return False

        try:
            actual_size = part_path.stat().st_size

            if task.total_size > 0:
                return actual_size == task.total_size

            return actual_size > 0
        except Exception:
            return False

    def pause_download(self, task: DownloadTask):
        asyncio_task = self._tasks.get(task.id)
        if asyncio_task and not asyncio_task.done():
            self._set_status(task, TaskStatus.PAUSED)
            asyncio_task.cancel()

    def resume_download(self, task: DownloadTask):
        if task.status in (TaskStatus.PAUSED, TaskStatus.FAILED):
            self._set_status(task, TaskStatus.QUEUED)

            asyncio_task = asyncio.create_task(self._run(task))
            self._tasks[task.id] = asyncio_task

    def cancel_download(self, task: DownloadTask):
        if task.is_terminal:
            return

        self._set_status(task, TaskStatus.CANCELLED, "Download was cancelled")
        asyncio_task = self._tasks.get(task.id)
        if asyncio_task and not asyncio_task.done():
            asyncio_task.cancel()

    def set_max_concurrent(self, value: int):
        if value == self._max_concurrent:
            return
        self._max_concurrent = value
        self._semaphore = asyncio.Semaphore(value)

    def set_speed_limit(self, bytes_per_sec: int):
        self._speed_limit_bytes_per_sec = max(0, bytes_per_sec)

    def get_incomplete_downloads(self) -> list[DownloadTask]:
        return [
            t for t in self._download_tasks
            if not t.is_terminal and t.download_url
        ]

    def pause_all(self):
        for task in list(self._download_tasks):
            if task.status in (
                TaskStatus.DOWNLOADING,
                TaskStatus.PREPARING,
                TaskStatus.VERIFYING,
                TaskStatus.QUEUED,
            ):
                self.pause_download(task)

    def resume_all(self):
        for task in list(self._download_tasks):
            if task.status == TaskStatus.PAUSED:
                self.resume_download(task)

    def cancel_all(self):
        for task in list(self._download_tasks):
            if not task.is_terminal:
                self.cancel_download(task)

    def clear_completed(self) -> list[str]:
        removed_ids = [
            t.id
            for t in self._download_tasks
            if t.status == TaskStatus.COMPLETED
        ]
        self._download_tasks = [
            t for t in self._download_tasks
            if t.status != TaskStatus.COMPLETED
        ]
        changed = self._update_queue_positions()
        for t in changed:
            self._emit_progress(t)
        return removed_ids

    def retry_task(self, task_id: str):
        task = self.find_task(task_id)
        if task is None or task.status != TaskStatus.FAILED:
            return
        self._prepare_retry(task)

    def retry_failed(self):
        for task in list(self._download_tasks):
            if task.status == TaskStatus.FAILED:
                self._prepare_retry(task)

    def _prepare_retry(self, task: DownloadTask):
        dest_path = Path(task.destination)
        part_path = dest_path.with_suffix(dest_path.suffix + ".part")
        has_part = part_path.exists() and part_path.stat().st_size > 0
        can_resume = (
            has_part
            and task.supports_resume
            and task.error_type not in (
                DownloadErrorType.HTML_RESPONSE,
                DownloadErrorType.ACCESS_DENIED,
                DownloadErrorType.RANGE_UNSUPPORTED,
            )
        )

        if can_resume:
            task.status = TaskStatus.PAUSED
            task.error = None
            task.error_type = DownloadErrorType.UNKNOWN
            self.resume_download(task)
        else:
            part_path.unlink(missing_ok=True)
            dest_path.unlink(missing_ok=True)
            task.downloaded_size = 0
            task.progress = 0.0
            task.speed = 0.0
            task.total_size = 0
            task.supports_resume = False
            task.error = None
            task.error_type = DownloadErrorType.UNKNOWN
            self._set_status(task, TaskStatus.QUEUED)
            asyncio_task = asyncio.create_task(self._run(task))
            self._tasks[task.id] = asyncio_task

    def restore_tasks(self, tasks: list[DownloadTask]):
        if self._download_tasks:
            return []

        ordered_tasks = sorted(
            tasks,
            key=lambda t: (
                0 if t.status == TaskStatus.QUEUED else 1,
                t.queue_order if t.queue_order > 0 else 999999,
                t.created_at,
            )
        )

        interrupted_tasks = []
        for task in ordered_tasks:
            if task.status in (
                TaskStatus.DOWNLOADING,
                TaskStatus.PREPARING,
                TaskStatus.VERIFYING,
            ):
                task.status = TaskStatus.PAUSED
                task.error = "Download was interrupted"
                task.error_type = DownloadErrorType.NETWORK
                interrupted_tasks.append(task)

            self._download_tasks.append(task)

        self._update_queue_positions()

        for task in self._download_tasks:
            self._emit_progress(task)

        return interrupted_tasks
