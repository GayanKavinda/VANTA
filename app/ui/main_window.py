import asyncio
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from app.core.app_state import AppState
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
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
        self._queue_controller = QueueController(
            self._app_state.download_manager
        )
        self._settings = SettingsService()
        self._download_service = DownloadService(
            analyzer=self._app_state.analyzer,
            download_manager=self._app_state.download_manager,
            queue_controller=self._queue_controller,
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

        self.downloads_page.set_queue_controller(
            self._queue_controller,
            self._download_service,
        )

        self.settings_page.set_services(
            self._app_state.download_manager,
            self._app_state.file_manager,
            self._settings,
        )

        self.stacked_widget.addWidget(self.home_page)
        self.stacked_widget.addWidget(self.downloads_page)
        self.stacked_widget.addWidget(self.history_page)
        self.stacked_widget.addWidget(self.settings_page)

        layout.addWidget(self.sidebar)
        layout.addWidget(self.stacked_widget, stretch=1)

        self.setCentralWidget(container)

    def _connect_signals(self):
        self.home_page.url_analyzed.connect(self._on_url_analyzed)
        self.home_page.download_requested.connect(self._on_download_requested)
        self.home_page.downloads_selected.connect(self._on_downloads_selected)
        self._create_actions()

    def _create_actions(self):
        quit_action = QAction("Exit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def _init_services(self):
        max_concurrent = self._settings.max_concurrent()
        self._app_state.download_manager.set_max_concurrent(max_concurrent)

        speed_limit_enabled = self._settings.get_bool("speed_limit_enabled")
        speed_limit_value = self._settings.get_int("speed_limit_value", 0)
        if speed_limit_enabled and speed_limit_value > 0:
            self._app_state.download_manager.set_speed_limit(speed_limit_value * 1024 * 1024)

        self._persistence.subscribe_to(self._app_state.download_manager)

    def _restore_tasks(self):
        tasks = load_download_tasks()

        interrupted_tasks = self._queue_controller.restore_tasks(tasks)
        self.history_page.refresh()

        if interrupted_tasks:
            self._prompt_recovery(interrupted_tasks)

        paused_tasks = [
            t for t in tasks
            if t.status == TaskStatus.PAUSED
        ]
        if paused_tasks:
            log.info("%d paused downloads available for manual resume", len(paused_tasks))

    def _prompt_recovery(self, tasks: list[DownloadTask]):
        from PySide6.QtWidgets import QMessageBox

        msg = (
            f"VANTA was closed during {len(tasks)} download(s).\n\n"
            "Resume these downloads?"
        )
        reply = QMessageBox.question(
            self,
            "Download Recovery",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if reply == QMessageBox.Yes:
            for task in tasks:
                self._queue_controller.resume_download(task)
            self.stacked_widget.setCurrentIndex(1)
            self.sidebar.set_active(1)

    def _on_page_changed(self, index: int):
        self.stacked_widget.setCurrentIndex(index)
        self.sidebar.set_active(index)

    def _on_url_analyzed(self, url: str):
        self.home_page.set_current_url_for_result(url)
        asyncio.create_task(self._analyze_only(url))

    def _on_settings_changed(self, key: str, value: str):
        log.info("Setting changed: %s = %s", key, value)
        if key == "max_concurrent":
            self._app_state.download_manager.set_max_concurrent(int(value))
        elif key == "speed_limit_enabled":
            enabled = value.lower() in ("true", "1", "yes")
            speed_limit_value = self._settings.get_int("speed_limit_value", 0)
            if enabled and speed_limit_value > 0:
                self._app_state.download_manager.set_speed_limit(speed_limit_value * 1024 * 1024)
            else:
                self._app_state.download_manager.set_speed_limit(0)
        elif key == "speed_limit_value":
            if self._settings.get_bool("speed_limit_enabled"):
                self._app_state.download_manager.set_speed_limit(int(value) * 1024 * 1024)

    async def _analyze_only(self, url: str):
        try:
            from app.services.analysis_view import build_view_model

            result, context = await self._download_service.analyze_url_with_context(url)
            view_model = build_view_model(result, context)
            self.home_page.show_analysis_view(view_model)

        except Exception as e:
            log.error("Failed to analyze URL: %s", e, exc_info=True)
            self.home_page.show_error(
                "Analysis Failed",
                "Could not process this URL. Check the logs for details.",
            )

    def _on_download_requested(self, source_url: str, resource_view):
        asyncio.create_task(self._start_resource_download(source_url, resource_view))

    def _on_downloads_selected(self, source_url: str, resource_views: list):
        if not resource_views:
            return
        asyncio.create_task(
            self._start_bulk_downloads(source_url, resource_views)
        )

    async def _start_resource_download(self, source_url: str, resource_view):
        try:
            task = await self._download_service.start_file_download(
                source_url=source_url,
                file=resource_view.file,
            )
            if task:
                self.stacked_widget.setCurrentIndex(1)
                self.sidebar.set_active(1)
                log.info("Download started: %s -> %s", task.id, task.name)
        except Exception as e:
            log.error("Failed to start download: %s", e, exc_info=True)
            self.home_page.show_error(
                "Download Failed",
                "Could not start this download. Check the logs.",
            )

    async def _start_bulk_downloads(self, source_url: str, resource_views: list):
        started = 0
        failed: list[str] = []
        for rv in resource_views:
            try:
                task = await self._download_service.start_file_download(
                    source_url=source_url,
                    file=rv.file,
                )
                if task:
                    started += 1
            except Exception as e:
                log.error(
                    "Failed to start selected download '%s': %s",
                    getattr(rv.file, "name", "?"), e, exc_info=True,
                )
                failed.append(getattr(rv.file, "name", "?"))

        if started:
            self.stacked_widget.setCurrentIndex(1)
            self.sidebar.set_active(1)
            log.info("Bulk download started: %d queued, %d failed", started, len(failed))
        if failed:
            self.home_page.show_error(
                "Some downloads could not start",
                "Failed to start: " + ", ".join(failed),
            )

    def closeEvent(self, event):
        self._persistence.flush()
        event.accept()
