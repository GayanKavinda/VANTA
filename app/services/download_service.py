from pathlib import Path
from typing import Optional

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import AnalysisResult, DownloadFile
from app.core.task_manager import DownloadTask, TaskStatus
from app.services.analyzer import AnalyzerService
from app.utils.logger import get_logger

log = get_logger("vanta.services.download_service")


class DownloadService:

    def __init__(
        self,
        analyzer: AnalyzerService,
        download_manager: DownloadManager,
        file_manager: FileManager,
    ):
        self._analyzer = analyzer
        self._download_manager = download_manager
        self._file_manager = file_manager

    @property
    def download_manager(self) -> DownloadManager:
        return self._download_manager

    async def analyze_url(self, url: str) -> AnalysisResult:
        return await self._analyzer.analyze(url)

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
    ) -> DownloadTask:
        dest_dir = Path(destination) if destination else self._file_manager.default_dir
        filename = self._file_manager.safe_join(file.name)
        dest_path = self._file_manager.get_unique_path(filename, dest_dir)

        task = await self._download_manager.add_download(
            name=file.name,
            source_url=source_url,
            download_url=file.url,
            destination=str(dest_path),
        )

        log.info(
            "Started download task '%s' (%s) -> %s",
            task.id, file.name, dest_path,
        )
        return task

    def get_all_tasks(self) -> list[DownloadTask]:
        return self._download_manager.download_tasks

    def pause_task(self, task_id: str):
        for task in self._download_manager.download_tasks:
            if task.id == task_id:
                self._download_manager.pause_download(task)
                break

    def resume_task(self, task_id: str):
        for task in self._download_manager.download_tasks:
            if task.id == task_id:
                self._download_manager.resume_download(task)
                break

    def cancel_task(self, task_id: str):
        for task in self._download_manager.download_tasks:
            if task.id == task_id:
                self._download_manager.cancel_download(task)
                break

    def pause_all(self):
        self._download_manager.pause_all()

    def resume_all(self):
        self._download_manager.resume_all()

    def cancel_all(self):
        self._download_manager.cancel_all()

    def clear_completed(self):
        self._download_manager.clear_completed()

    def retry_failed(self):
        self._download_manager.retry_failed()

    def retry_task(self, task_id: str):
        self._download_manager.retry_task(task_id)