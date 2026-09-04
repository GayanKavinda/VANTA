import asyncio
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from app.core.app_state import AppState
from app.core.task_manager import TaskStatus
from app.database.repositories import load_download_tasks
from app.services.download_service import DownloadService
from app.services.persistence_service import PersistenceService
from app.services.settings_service import SettingsService
from app.ui.pages.home_page import HomePage
from app.ui.pages.downloads_page import DownloadsPage
from app.ui.pages.history_page import HistoryPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.widgets.sidebar import Sidebar
from app.utils.logger import get_logger

log = get_logger("vanta.ui.main_window")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("VANTA")
        self.resize(1200, 750)

        self._app_state = AppState()
        self._settings = SettingsService()
        self._download_service = DownloadService(
            analyzer=self._app_state.analyzer,
            download_manager=self._app_state.download_manager,
            file_manager=self._app_state.file_manager,
        )
        self._persistence = PersistenceService()

        self._build_ui()
        self._connect_signals()
        self._init_services()
        self._restore_tasks()

    def _build_ui(self):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.page_changed.connect(self._on_page_changed)

        self.stacked_widget = QStackedWidget()

        self.home_page = HomePage()
        self.downloads_page = DownloadsPage()
        self.history_page = HistoryPage()
        self.settings_page = SettingsPage()
        self.settings_page.settings_changed.connect(self._on_settings_changed)

        self.downloads_page.set_download_manager(self._app_state.download_manager)

        self.stacked_widget.addWidget(self.home_page)
        self.stacked_widget.addWidget(self.downloads_page)
        self.stacked_widget.addWidget(self.history_page)
        self.stacked_widget.addWidget(self.settings_page)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.stacked_widget, stretch=1)

        self.setCentralWidget(container)

    def _connect_signals(self):
        self.home_page.url_analyzed.connect(self._on_url_analyzed)

    def _init_services(self):
        max_concurrent = self._settings.max_concurrent()
        self._app_state.download_manager.set_max_concurrent(max_concurrent)
        self._persistence.subscribe_to(self._app_state.download_manager)

    def _restore_tasks(self):
        tasks = load_download_tasks()
        for task in tasks:
            if not task.is_terminal:
                self._app_state.download_manager._download_tasks.append(task)
            self._app_state.download_manager._emit_progress(task)

        self.history_page.refresh()

    def _on_page_changed(self, index: int):
        self.stacked_widget.setCurrentIndex(index)
        self.sidebar.set_active(index)

    def _on_url_analyzed(self, url: str):
        self.home_page.set_current_url_for_result(url)
        asyncio.create_task(self._analyze_and_start(url))

    def _on_settings_changed(self, key: str, value: str):
        log.info("Setting changed: %s = %s", key, value)
        if key == "max_concurrent":
            self._app_state.download_manager.set_max_concurrent(int(value))

    async def _analyze_and_start(self, url: str):
        try:
            result = await self._download_service.analyze_url(url)
            self.home_page.show_analysis_result(result)

            if result.status == "ready" and result.files:
                task = await self._download_service.start_download(url)
                if task:
                    self.stacked_widget.setCurrentIndex(1)
                    self.sidebar.set_active(1)
                    log.info("Download started: %s", task.id)
            elif result.status in ("unsupported", "error"):
                log.warning("Source not supported or analysis failed for URL: %s", url)

        except Exception as e:
            log.error("Failed to analyze URL: %s", e, exc_info=True)
            self.home_page.show_error(
                "Analysis Failed",
                f"Could not process this URL. Check the logs for details.",
            )
