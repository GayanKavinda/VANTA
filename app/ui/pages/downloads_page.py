from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.task_manager import DownloadTask, TaskStatus
from app.core.downloader import DownloadManager
from app.ui.widgets.download_card import DownloadCard
from app.utils.logger import get_logger

log = get_logger("vanta.ui.downloads")


class DownloadsPage(QWidget):
    def __init__(self):
        super().__init__()

        self._cards: dict[str, DownloadCard] = {}
        self._download_manager: DownloadManager | None = None
        self._download_service = None

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

        self._placeholder = QLabel("No active downloads")
        self._placeholder.setStyleSheet("color: #8A8A9A; padding: 20px;")
        self._content_layout.addWidget(self._placeholder)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(self._content)

        layout.addWidget(scroll, stretch=1)

    def set_download_manager(self, manager: DownloadManager, service=None):
        self._download_manager = manager
        self._download_service = service
        manager.add_progress_callback(self._on_progress)
        self._update_summary()

    def _on_progress(self, task: DownloadTask):
        if task.id not in self._cards:
            if task.is_terminal and task.status == TaskStatus.COMPLETED:
                return
            card = DownloadCard(task)
            card.pause_requested.connect(self._on_pause_clicked)
            card.resume_requested.connect(self._on_resume_clicked)
            card.cancel_requested.connect(self._on_cancel_clicked)
            card.retry_requested.connect(self._on_retry_clicked)
            self._cards[task.id] = card
            self._placeholder.setVisible(False)
            self._content_layout.insertWidget(self._content_layout.count() - 1, card)

        if task.id in self._cards:
            self._cards[task.id].update_from_task(task)

        self._update_summary()

    def _update_summary(self):
        manager = self._download_manager
        if manager is None:
            return

        active = sum(
            1 for t in manager.download_tasks
            if t.status in (
                TaskStatus.PREPARING,
                TaskStatus.DOWNLOADING,
                TaskStatus.VERIFYING,
            )
        )
        queued = sum(
            1 for t in manager.download_tasks
            if t.status == TaskStatus.QUEUED
        )
        paused = sum(
            1 for t in manager.download_tasks
            if t.status == TaskStatus.PAUSED
        )
        completed = sum(
            1 for t in manager.download_tasks
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

    def _get_manager(self):
        return self._download_manager

    def _on_pause_all(self):
        if self._download_service:
            self._download_service.pause_all()

    def _on_resume_all(self):
        if self._download_service:
            self._download_service.resume_all()

    def _on_cancel_all(self):
        if self._download_service:
            self._download_service.cancel_all()

    def _on_retry_failed(self):
        if self._download_service:
            self._download_service.retry_failed()

    def _on_clear_completed(self):
        if self._download_service:
            self._download_service.clear_completed()
        manager = self._get_manager()
        if manager is None:
            return
        for task_id in list(self._cards.keys()):
            task = manager.find_task(task_id)
            if task is None or task.is_terminal:
                card = self._cards.pop(task_id)
                card.deleteLater()
        if not self._cards:
            self._placeholder.setVisible(True)
        self._update_summary()

    def _on_pause_clicked(self, task_id: str):
        manager = self._get_manager()
        if manager is None:
            return
        task = manager.find_task(task_id)
        if task is not None:
            manager.pause_download(task)

    def _on_resume_clicked(self, task_id: str):
        manager = self._get_manager()
        if manager is None:
            return
        task = manager.find_task(task_id)
        if task is not None:
            manager.resume_download(task)

    def _on_cancel_clicked(self, task_id: str):
        manager = self._get_manager()
        if manager is None:
            return
        task = manager.find_task(task_id)
        if task is not None:
            manager.cancel_download(task)

    def _on_retry_clicked(self, task_id: str):
        manager = self._get_manager()
        if manager is None:
            return
        task = manager.find_task(task_id)
        if task is not None and task.status == TaskStatus.FAILED:
            manager.retry_failed()
