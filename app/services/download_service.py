from pathlib import Path
from typing import Optional

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import AnalysisResult, DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
from app.services.analyzer import AnalyzerService
from app.services.filename_service import is_safe_within_directory
from app.utils.logger import get_logger

log = get_logger("vanta.services.download_service")


class DownloadService:

    def __init__(
        self,
        analyzer: AnalyzerService,
        download_manager: DownloadManager,
        file_manager: FileManager,
        queue_controller: QueueController | None = None,
    ):
        self._analyzer = analyzer
        self._download_manager = download_manager
        self._file_manager = file_manager
        self._queue_controller = queue_controller

    @property
    def download_manager(self) -> DownloadManager:
        return self._download_manager

    @property
    def queue_controller(self) -> QueueController | None:
        return self._queue_controller

    @property
    def analyzer(self) -> AnalyzerService:
        return self._analyzer

    async def analyze_url(self, url: str) -> AnalysisResult:
        return await self._analyzer.analyze(url)

    async def analyze_url_with_context(self, url: str):
        return await self._analyzer.analyze_with_context(url)

    async def start_download(
        self,
        url: str,
        destination: str | Path | None = None,
        result: AnalysisResult | None = None,
    ) -> DownloadTask | None:
        if result is None:
            result = await self._analyzer.analyze(url)

        if not result.files:
            log.warning("No files found in analysis result for URL: %s", url)
            return None

        first_file = result.files[0]
        return await self.start_file_download(
            source_url=url,
            file=first_file,
            destination=destination,
        )

    async def start_file_download(
        self,
        source_url: str,
        file: DownloadFile,
        destination: str | Path | None = None,
        filename: str | None = None,
    ) -> DownloadTask | None:
        from app.core.models import ResolvedResource
        if isinstance(file, ResolvedResource):
            resolved: ResolvedResource = file
            resolved_file = DownloadFile(
                name=resolved.filename or "download",
                url=resolved.final_url,
                size=resolved.size,
                content_type=resolved.content_type,
            )
            file = resolved_file

        name_to_use = filename if filename else file.name
        safe_name = self._file_manager.safe_join(name_to_use)
        dest_dir = Path(destination) if destination else self._file_manager.default_dir

        # Defense-in-depth validation (requirement #6).
        if not safe_name:
            log.warning("Download rejected: empty filename after sanitization")
            return None
        if not is_safe_within_directory(safe_name, dest_dir):
            log.warning("Download rejected: filename escapes destination: %s", safe_name)
            return None
        if filename and destination:
            dest_dir.mkdir(parents=True, exist_ok=True)
        # Review dialog flow: explicit filename + destination.
        # Use exact reviewed values; reject if target already exists.
        if filename and destination:
            dest_path = dest_dir / safe_name
            if dest_path.exists() and dest_path.is_file() and dest_path.stat().st_size > 0:
                log.warning("Download rejected: file already exists: %s", dest_path)
                return None
        else:
            # Non-review flow: auto-resolve unique path (backward compatible).
            dest_path = self._file_manager.get_unique_path(safe_name, dest_dir)

        existing = self._find_existing_task(file.url, str(dest_path))
        if existing is not None:
            log.info(
                "Download already registered; not duplicating queue entry: %s -> %s (%s)",
                file.url, dest_path, existing.id,
            )
            return existing

        if self._queue_controller is not None:
            task = await self._queue_controller.add_download(
                name=name_to_use,
                source_url=source_url,
                download_url=file.url,
                destination=str(dest_path),
            )
        else:
            task = await self._download_manager.add_download(
                name=name_to_use,
                source_url=source_url,
                download_url=file.url,
                destination=str(dest_path),
            )

        log.info("Started download task '%s' (%s) -> %s", task.id, name_to_use, dest_path)
        return task

    def get_all_tasks(self) -> list[DownloadTask]:
        return self._download_manager.download_tasks

    def _find_existing_task(
        self, download_url: str, destination: str
    ) -> Optional[DownloadTask]:
        """Return an existing non-terminal task for the same download URL and
        destination, or ``None``.

        This is the exactly-once queue registration guard for Phase 4.1.1:
        when a reviewed download (explicit filename + destination) is
        submitted more than once, the previously registered task is returned
        instead of creating a duplicate queue entry. Identity is based on
        ``download_url`` + reviewed ``destination`` — both established by the
        review layer — so the queue never rediscovers or re-analyzes the
        resource.
        """
        if not download_url:
            return None
        for task in self.get_all_tasks():
            if task.is_terminal:
                continue
            if task.download_url == download_url and task.destination == destination:
                return task
        return None

    def pause_task(self, task_id: str):
        task = self._lookup(task_id)
        if task is not None:
            if self._queue_controller is not None:
                self._queue_controller.pause_download(task)
            else:
                self._download_manager.pause_download(task)

    def resume_task(self, task_id: str):
        task = self._lookup(task_id)
        if task is not None:
            if self._queue_controller is not None:
                self._queue_controller.resume_download(task)
            else:
                self._download_manager.resume_download(task)

    def cancel_task(self, task_id: str):
        task = self._lookup(task_id)
        if task is not None:
            if self._queue_controller is not None:
                self._queue_controller.cancel_download(task)
            else:
                self._download_manager.cancel_download(task)

    def pause_all(self):
        if self._queue_controller is not None:
            self._queue_controller.pause_all()
        else:
            self._download_manager.pause_all()

    def resume_all(self):
        if self._queue_controller is not None:
            self._queue_controller.resume_all()
        else:
            self._download_manager.resume_all()

    def cancel_all(self):
        if self._queue_controller is not None:
            self._queue_controller.cancel_all()
        else:
            self._download_manager.cancel_all()

    def clear_completed(self):
        if self._queue_controller is not None:
            return self._queue_controller.clear_completed()
        return self._download_manager.clear_completed()

    def retry_failed(self):
        if self._queue_controller is not None:
            self._queue_controller.retry_failed()
        else:
            self._download_manager.retry_failed()

    def retry_task(self, task_id: str):
        if self._queue_controller is not None:
            self._queue_controller.retry_download(task_id)
        else:
            self._download_manager.retry_task(task_id)

    def _lookup(self, task_id: str) -> DownloadTask | None:
        manager = (
            self._queue_controller
            if self._queue_controller is not None
            else self._download_manager
        )
        return manager.find_task(task_id)
