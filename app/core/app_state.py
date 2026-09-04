from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.services.analyzer import AnalyzerService
from app.services.source_detector import SourceDetector

from app.utils.logger import get_logger

log = get_logger("vanta.app_state")


class AppState:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        from app.utils.constants import DEFAULT_DOWNLOAD_DIR, DEFAULT_CONCURRENT_DOWNLOADS

        self._file_manager = FileManager(DEFAULT_DOWNLOAD_DIR)
        self._source_detector = SourceDetector()
        self._analyzer = AnalyzerService(self._source_detector)
        self._download_manager = DownloadManager(
            max_concurrent=DEFAULT_CONCURRENT_DOWNLOADS
        )

    @property
    def file_manager(self) -> FileManager:
        return self._file_manager

    @property
    def source_detector(self) -> SourceDetector:
        return self._source_detector

    @property
    def analyzer(self) -> AnalyzerService:
        return self._analyzer

    @property
    def download_manager(self) -> DownloadManager:
        return self._download_manager


def get_app_state() -> AppState:
    return AppState()
