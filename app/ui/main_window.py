import asyncio
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from app.core.app_state import AppState
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
from app.database.repositories import load_download_tasks
from app.services.download_service import DownloadService
from app.services.download_workflow import DownloadWorkflowService
from app.services.persistence_service import PersistenceService
from app.services.scheduling_service import SchedulingService
from app.services.settings_service import SettingsService
from app.ui.bulk_review import BulkReviewDialog
from app.ui.download_review import DownloadReviewDialog
from app.ui.icon import icon
from app.ui.pages.home_page import HomePage
from app.ui.pages.downloads_page import DownloadsPage
from app.ui.pages.history_page import HistoryPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.theme import ThemeService
from app.ui.widgets.sidebar import Sidebar
from app.utils.logger import get_logger

log = get_logger("vanta.ui.main_window")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("VANTA")
        self.setWindowIcon(icon())
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
        self._scheduling = SchedulingService(self._queue_controller)
        self._theme = ThemeService(self._settings)

        # V2.0 Phase 2 - workflow service for duplicate detection/validation.
        self._workflow = DownloadWorkflowService(self._app_state.file_manager)

        self._build_ui()
        self._connect_signals()
        self._init_services()
        self._restore_tasks()
        self._theme.apply()

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

        self.history_page.set_queue_controller(self._queue_controller)
        self.history_page.set_file_manager(self._app_state.file_manager)

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
        self._queue_controller.set_max_concurrent(max_concurrent)

        # speed_limit_bytes_per_sec() returns 0 (unlimited) when disabled/zero,
        # and otherwise the per-download MB/s value converted to bytes/sec.
        self._app_state.download_manager.set_speed_limit(
            self._settings.speed_limit_bytes_per_sec()
        )

        self._persistence.subscribe_to(self._app_state.download_manager)

    def _restore_tasks(self):
        # Fix database consistency issues first
        self._persistence.fix_database()

        tasks = load_download_tasks()

        # Completed files remain available in History, not the active queue.
        tasks_for_queue = [
            task for task in tasks
            if task.status != TaskStatus.COMPLETED
        ]
        interrupted_tasks = self._queue_controller.restore_tasks(tasks_for_queue)
        self._scheduling.start()
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
            self._queue_controller.set_max_concurrent(int(value))
        elif key == "download_dir":
            from pathlib import Path

            self._app_state.file_manager.set_default_dir(Path(value))
        elif key == "theme":
            self._theme.apply(value)
        elif key in ("speed_limit_enabled", "speed_limit_value"):
            # The speed limit is PER DOWNLOAD. speed_limit_bytes_per_sec()
            # returns 0 (unlimited) when disabled or zero, otherwise the
            # per-download MB/s value converted to bytes/sec.
            self._app_state.download_manager.set_speed_limit(
                self._settings.speed_limit_bytes_per_sec()
            )
        elif key in ("launch_on_startup", "check_for_updates"):
            # These preferences are persisted for compatibility but have no
            # runtime infrastructure behind them in this phase. The stored
            # value is honored on disk; no action is taken.
            log.info(
                "Dormant setting %s updated to %s (no runtime action in Phase 4.10)",
                key,
                value,
            )

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
        # V2.0 Phase 2 - compute duplicate state before opening review.
        from app.services.filename_service import sanitize_filename

        proposed_filename = sanitize_filename(resource_view.file.name)
        proposed_destination = self._settings.download_dir()
        duplicate_check = self._workflow.check_duplicate(
            resource_view.file, proposed_filename, proposed_destination
        )

        # V2.0 Phase 2 - show review dialog before starting.
        dialog = DownloadReviewDialog(
            file=resource_view.file,
            file_manager=self._app_state.file_manager,
            download_dir=proposed_destination,
            duplicate_check=duplicate_check,
            workflow=self._workflow,
            conflict_policy=self._settings.conflict_policy(),
            parent=self,
        )
        if dialog.exec() != 1:
            log.info("Download cancelled by user during review")
            return

        asyncio.create_task(
            self._start_resource_download(source_url, resource_view, dialog)
        )

    def _on_downloads_selected(self, source_url: str, resource_views: list):
        if not resource_views:
            return
        # V2.0 Phase 3.8 — route selected resources through the existing
        # review workflow before queueing. The UI never invokes the downloader
        # directly; it emits a signal handled here.
        proposed_dest = self._settings.download_dir()
        dialog = BulkReviewDialog(
            source_url=source_url,
            resource_views=resource_views,
            file_manager=self._app_state.file_manager,
            download_dir=proposed_dest,
            workflow=self._workflow,
            conflict_policy=self._settings.conflict_policy(),
            parent=self,
        )
        if dialog.exec() != 1:
            log.info("Bulk review cancelled by user; returning to analysis")
            return
        asyncio.create_task(
            self._start_reviewed_bulk_downloads(source_url, dialog.accepted_entries)
        )

    async def _start_resource_download(self, source_url: str, resource_view, dialog: DownloadReviewDialog | None = None):
        try:
            # V2.0 Phase 2 - use reviewed filename/destination when available.
            if dialog is not None:
                task = await self._download_service.start_file_download(
                    source_url=source_url,
                    file=resource_view.file,
                    destination=dialog.destination,
                    filename=dialog.filename,
                    scheduled_at=dialog.scheduled_at,
                )
            else:
                task = await self._download_service.start_file_download(
                    source_url=source_url,
                    file=resource_view.file,
                )
            if task:
                self._scheduling.track(task.id)
                # Register the task so future duplicate checks see it.
                self._workflow.register_selected(task)
                self.stacked_widget.setCurrentIndex(1)
                self.sidebar.set_active(1)
                log.info("Download started: %s -> %s", task.id, task.name)
        except Exception as e:
            log.error("Failed to start download: %s", e, exc_info=True)
            self.home_page.show_error(
                "Download Failed",
                "Could not start this download. Check the logs.",
            )

    async def _start_reviewed_bulk_downloads(
        self,
        source_url: str,
        accepted: list,
    ):
        """Queue each reviewed, READY resource via the reviewed filename+dest
        flow (DownloadService.start_file_download). Reuses the existing
        register_selected + queue path. Only resources the review workflow
        considered ready are queued.
        """
        started = 0
        failed: list[str] = []
        for entry, filename, dest in accepted:
            rv = entry.view
            try:
                task = await self._download_service.start_file_download(
                    source_url=source_url,
                    file=rv.file,
                    destination=str(dest),
                    filename=filename,
                )
                if task:
                    # Register the task so future duplicate checks see it.
                    self._workflow.register_selected(task)
                    started += 1
                else:
                    failed.append(getattr(rv.file, "name", "?"))
            except Exception as e:
                log.error(
                    "Failed to start selected download '%s': %s",
                    getattr(rv.file, "name", "?"),
                    e,
                    exc_info=True,
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
        # Window close - graceful shutdown is handled via QApplication.aboutToQuit
        # and qasync.asyncClose registered in main.py
        event.accept()
