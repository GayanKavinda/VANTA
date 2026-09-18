import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
from app.services.download_service import DownloadService
from app.ui.download_details import DownloadDetailsDialog
from app.ui.widgets.download_card import DownloadCard
from app.utils.logger import get_logger

log = get_logger("vanta.ui.downloads")


class DownloadsPage(QWidget):
    def __init__(self):
        super().__init__()

        self._cards: dict[str, DownloadCard] = {}
        self._queue_controller: QueueController | None = None
        self._download_service: DownloadService | None = None
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(1000)
        self._schedule_timer.timeout.connect(self._refresh_scheduled_cards)
        self._schedule_timer.start()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(16)

        title = QLabel("Downloads")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        self._summary = QLabel("0 Active · 0 Queued · 0 Completed")
        self._summary.setStyleSheet("font-size: 13px; color: #8A8A9A;")
        layout.addWidget(self._summary)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._pause_all_btn = QPushButton("Pause All")
        self._pause_all_btn.setFixedSize(100, 32)
        self._pause_all_btn.clicked.connect(self._on_pause_all)
        actions.addWidget(self._pause_all_btn)

        self._resume_all_btn = QPushButton("Resume All")
        self._resume_all_btn.setFixedSize(100, 32)
        self._resume_all_btn.clicked.connect(self._on_resume_all)
        actions.addWidget(self._resume_all_btn)

        self._cancel_all_btn = QPushButton("Cancel All")
        self._cancel_all_btn.setFixedSize(100, 32)
        self._cancel_all_btn.clicked.connect(self._on_cancel_all)
        actions.addWidget(self._cancel_all_btn)

        self._retry_failed_btn = QPushButton("Retry Failed")
        self._retry_failed_btn.setFixedSize(100, 32)
        self._retry_failed_btn.clicked.connect(self._on_retry_failed)
        actions.addWidget(self._retry_failed_btn)

        self._clear_completed_btn = QPushButton("Clear Completed")
        self._clear_completed_btn.setFixedSize(110, 32)
        self._clear_completed_btn.clicked.connect(self._on_clear_completed)
        actions.addWidget(self._clear_completed_btn)

        self._clear_history_btn = QPushButton("Clear All")
        self._clear_history_btn.setFixedSize(100, 32)
        self._clear_history_btn.clicked.connect(self._on_clear_history)
        actions.addWidget(self._clear_history_btn)

        actions.addStretch(1)
        layout.addLayout(actions)

        self._content = QFrame()
        self._content.setObjectName("downloads_content")
        self._content.setStyleSheet("""
            QFrame#downloads_content {
                background: #0F1014;
            }
        """)

        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignTop)

        self._placeholder = QLabel("No downloads")
        self._placeholder.setStyleSheet("color: #8A8A9A; padding: 20px;")
        self._content_layout.addWidget(self._placeholder)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(self._content)

        layout.addWidget(scroll, stretch=1)

    def set_queue_controller(
        self,
        controller: QueueController,
        service: DownloadService | None = None,
    ):
        self._queue_controller = controller
        self._download_service = service
        controller.add_queue_callback(self._on_queue_changed)
        self._update_summary()

    def _on_queue_changed(self, task: DownloadTask | None):
        if task is None:
            self._update_summary()
            return

        if task.id not in self._cards:
            card = DownloadCard(task)
            card.pause_requested.connect(self._on_pause_clicked)
            card.resume_requested.connect(self._on_resume_clicked)
            card.cancel_requested.connect(self._on_cancel_clicked)
            card.retry_requested.connect(self._on_retry_clicked)
            card.open_requested.connect(self._on_open_folder_clicked)
            card.open_file_requested.connect(self._on_open_file_clicked)
            card.remove_requested.connect(self._on_remove_clicked)
            card.move_up_requested.connect(self._on_move_up_clicked)
            card.move_down_requested.connect(self._on_move_down_clicked)
            card.move_top_requested.connect(self._on_move_top_clicked)
            card.move_bottom_requested.connect(self._on_move_bottom_clicked)
            card.details_requested.connect(self._on_details_clicked)
            self._cards[task.id] = card
            self._placeholder.setVisible(False)
            self._content_layout.insertWidget(
                self._content_layout.count() - 1, card
            )

        if task.id in self._cards:
            self._cards[task.id].update_from_task(task)

        self._update_summary()

    def _update_summary(self):
        controller = self._queue_controller
        if controller is None:
            return

        active = sum(
            1 for t in controller.tasks
            if t.status in (
                TaskStatus.PREPARING,
                TaskStatus.DOWNLOADING,
                TaskStatus.VERIFYING,
            )
        )
        queued = controller.queued_count
        paused = sum(
            1 for t in controller.tasks
            if t.status == TaskStatus.PAUSED
        )
        completed = sum(
            1 for t in controller.tasks
            if t.status == TaskStatus.COMPLETED
        )

        parts = []
        if active:
            parts.append(f"{active} Active")
        if queued:
            parts.append(f"{queued} Queued")
        if paused:
            parts.append(f"{paused} Paused")
        if completed:
            parts.append(f"{completed} Completed")

        self._summary.setText(" · ".join(parts) if parts else "No downloads")

    def _refresh_scheduled_cards(self):
        controller = self._queue_controller
        if controller is None:
            return
        for task_id, card in list(self._cards.items()):
            task = controller.find_task(task_id)
            if task is not None and task.scheduled_at is not None:
                card.update_from_task(task)

    def closeEvent(self, event):
        self._schedule_timer.stop()
        super().closeEvent(event)

    def _on_pause_all(self):
        if self._queue_controller:
            self._queue_controller.pause_all()

    def _on_resume_all(self):
        if self._queue_controller:
            self._queue_controller.resume_all()

    def _on_cancel_all(self):
        if self._queue_controller:
            self._queue_controller.cancel_all()

    def _on_retry_failed(self):
        if self._queue_controller:
            self._queue_controller.retry_failed()

    def _on_clear_completed(self):
        if self._queue_controller is None:
            return

        completed_tasks = [
            task for task in self._queue_controller.tasks
            if task.status == TaskStatus.COMPLETED
        ]
        if self._download_service is not None:
            for task in completed_tasks:
                if task.destination:
                    destination = Path(task.destination)
                    if destination.is_file():
                        self._download_service.file_manager.delete_file(destination)

        removed_ids = self._queue_controller.clear_completed()

        for task_id in removed_ids:
            card = self._cards.pop(task_id, None)
            if card is not None:
                card.deleteLater()

        for task_id in list(self._cards.keys()):
            task = self._queue_controller.find_task(task_id)
            if task is not None and task.status == TaskStatus.COMPLETED:
                card = self._cards.pop(task_id)
                card.deleteLater()

        if not self._cards:
            self._placeholder.setVisible(True)
        self._update_summary()

    def _on_clear_history(self):
        controller = self._queue_controller
        if controller is None:
            return

        tasks = list(controller.tasks)
        for task in tasks:
            if (
                task.destination
                and self._download_service is not None
                and Path(task.destination).is_file()
            ):
                self._download_service.file_manager.delete_file(Path(task.destination))
            controller.remove_task(task.id)

        for task in tasks:
            card = self._cards.pop(task.id, None)
            if card is not None:
                card.deleteLater()

        if not self._cards:
            self._placeholder.setVisible(True)
        self._update_summary()

    def _on_pause_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is not None:
            controller.pause_download(task)

    def _on_resume_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is not None:
            controller.resume_download(task)

    def _on_cancel_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is not None:
            controller.cancel_download(task)

    def _on_retry_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is not None and task.status == TaskStatus.FAILED:
            controller.retry_download(task_id)

    def _on_move_up_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        controller.move_task_up(task_id)

    def _on_move_down_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        controller.move_task_down(task_id)

    def _on_move_top_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        controller.move_task_to_top(task_id)

    def _on_move_bottom_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        controller.move_task_to_bottom(task_id)

    def _on_open_folder_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is not None:
            dest_dir = str(Path(task.destination).parent)
            if dest_dir and os.path.isdir(dest_dir):
                QDesktopServices.openUrl(QUrl.fromLocalFile(dest_dir))

    def _on_open_file_clicked(self, task_id: str):
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is not None:
            dest_path = task.destination
            if dest_path and os.path.isfile(dest_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(dest_path))

    def _on_remove_clicked(self, task_id: str):
        if self._queue_controller is None:
            return
        task = self._queue_controller.find_task(task_id)
        if task is not None and task.destination:
            destination = Path(task.destination)
            if destination.is_file() and self._download_service is not None:
                self._download_service.file_manager.delete_file(destination)
        card = self._cards.pop(task_id, None)
        if card is not None:
            card.deleteLater()
        self._queue_controller.remove_task(task_id)

        if not self._cards:
            self._placeholder.setVisible(True)
        self._update_summary()

    def _on_details_clicked(self, task_id: str):
        """Open the download-details dialog for a task."""
        controller = self._queue_controller
        if controller is None:
            return
        task = controller.find_task(task_id)
        if task is None:
            return
        dialog = DownloadDetailsDialog(task, parent=self)
        dialog.exec()
