import asyncio
import os
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from app.core.task_manager import DownloadTask, TaskStatus
from app.utils.constants import CHUNK_SIZE
from app.utils.logger import get_logger

log = get_logger("vanta.core.downloader")

ProgressCallback = Callable[[DownloadTask], None]


class DownloadManager:

    def __init__(self, max_concurrent: int = 3):
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, asyncio.Task] = {}
        self._download_tasks: list[DownloadTask] = []
        self._callbacks: list[ProgressCallback] = []

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
            task = DownloadTask(
                id=str(int(time.time() * 1000))[-8:],
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

        self._emit_progress(task)

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
                    task.status = TaskStatus.CANCELLED
                    task.error = "Download was cancelled"
                raise
            except Exception as e:
                log.error("Download failed for '%s': %s", task.name, e, exc_info=True)
                task.status = TaskStatus.FAILED
                task.error = str(e)
            finally:
                self._tasks.pop(task.id, None)
                if not was_paused:
                    self._emit_progress(task)

    async def _execute_download(self, task: DownloadTask):
        task.status = TaskStatus.PREPARING
        self._emit_progress(task)

        dest_path = Path(task.destination)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        resume_position = 0
        file_mode = "wb"

        if dest_path.exists():
            resume_position = dest_path.stat().st_size
            if resume_position > 0:
                file_mode = "ab"

        async with httpx.AsyncClient(follow_redirects=True) as client:
            headers = {}
            if resume_position > 0:
                headers["Range"] = f"bytes={resume_position}-"

            async with client.stream(
                "GET",
                task.download_url,
                headers=headers,
                timeout=httpx.Timeout(60),
            ) as response:
                if response.status_code == 416:
                    resume_position = 0
                    file_mode = "wb"
                    dest_path.unlink(missing_ok=True)
                    async with client.stream(
                        "GET", task.download_url, timeout=httpx.Timeout(60)
                    ) as r2:
                        await self._stream_to_file(r2, task, dest_path, 0, "wb")
                    return

                if response.status_code not in (200, 206):
                    response.raise_for_status()

                task.supports_resume = response.status_code == 206 and resume_position > 0

                await self._stream_to_file(response, task, dest_path, resume_position, file_mode)

        task.status = TaskStatus.VERIFYING
        self._emit_progress(task)

        if self._verify_file(dest_path, task):
            task.status = TaskStatus.COMPLETED
        else:
            task.status = TaskStatus.FAILED
            task.error = "File verification failed"

        self._emit_progress(task)

    async def _stream_to_file(
        self,
        response,
        task: DownloadTask,
        dest_path: Path,
        resume_position: int,
        file_mode: str,
    ):
        total_size = int(response.headers.get("content-length", 0))

        if resume_position > 0:
            if total_size > 0:
                total_size += resume_position
        elif total_size > 0:
            pass

        if total_size > 0:
            task.total_size = total_size

        task.status = TaskStatus.DOWNLOADING
        self._emit_progress(task)

        downloaded = resume_position
        last_emit = downloaded
        start_time = time.monotonic()

        with open(dest_path, file_mode) as f:
            async for chunk in response.aiter_bytes(CHUNK_SIZE):
                f.write(chunk)
                f.flush()

                downloaded += len(chunk)
                elapsed = time.monotonic() - start_time
                speed = downloaded / elapsed if elapsed > 0 else 0.0

                task.downloaded_size = downloaded
                if total_size > 0:
                    task.progress = round((downloaded / total_size) * 100, 1)
                task.speed = speed
                task.updated_at = time.time()

                if downloaded - last_emit >= CHUNK_SIZE:
                    self._emit_progress(task)
                    last_emit = downloaded

        self._emit_progress(task)

    def _verify_file(self, dest_path: Path, task: DownloadTask) -> bool:
        if not dest_path.exists():
            return False

        try:
            actual_size = dest_path.stat().st_size
            if task.total_size > 0:
                return actual_size >= task.total_size
            return actual_size > 0
        except Exception:
            return False

    def pause_download(self, task: DownloadTask):
        asyncio_task = self._tasks.get(task.id)
        if asyncio_task and not asyncio_task.done():
            task.status = TaskStatus.PAUSED
            asyncio_task.cancel()
            self._emit_progress(task)

    def resume_download(self, task: DownloadTask):
        if task.status == TaskStatus.PAUSED or task.status == TaskStatus.FAILED:
            asyncio_task = asyncio.create_task(self._run(task))
            self._tasks[task.id] = asyncio_task

    def cancel_download(self, task: DownloadTask):
        asyncio_task = self._tasks.get(task.id)
        if asyncio_task and not asyncio_task.done():
            asyncio_task.cancel()
        task.status = TaskStatus.CANCELLED
        task.error = "Download was cancelled"
        self._emit_progress(task)

    def set_max_concurrent(self, value: int):
        self._max_concurrent = value
        self._semaphore = asyncio.Semaphore(value)
