from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(16)

        title = QLabel("Downloads")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        layout.addWidget(title)

        self._summary = QLabel("0 Active · 0 Completed")
        self._summary.setStyleSheet("font-size: 13px; color: #8A8A9A;")
        layout.addWidget(self._summary)

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

    def set_download_manager(self, manager: DownloadManager):
        manager.add_progress_callback(self._on_progress)

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

        elif task.status == TaskStatus.COMPLETED and task in list(self._cards.keys()):
            card = self._cards[task.id]
            card.update_from_task(task)

        if task.id in self._cards:
            self._cards[task.id].update_from_task(task)

        self._update_summary()

    def _update_summary(self):
        active = sum(
            1 for t in self._cards.values()
            if t._task.status in (
                TaskStatus.QUEUED, TaskStatus.PREPARING,
                TaskStatus.DOWNLOADING, TaskStatus.PAUSED, TaskStatus.VERIFYING,
            )
        )
        completed = sum(
            1 for t in self._cards.values()
            if t._task.status == TaskStatus.COMPLETED
        )
        self._summary.setText(f"{active} Active · {completed} Completed")

    def _on_pause_clicked(self, task_id: str):
        from app.core.app_state import get_app_state
        get_app_state().download_manager.pause_download(
            next(t for t in get_app_state().download_manager.download_tasks if t.id == task_id)
        )

    def _on_resume_clicked(self, task_id: str):
        from app.core.app_state import get_app_state
        get_app_state().download_manager.resume_download(
            next(t for t in get_app_state().download_manager.download_tasks if t.id == task_id)
        )

    def _on_cancel_clicked(self, task_id: str):
        from app.core.app_state import get_app_state
        get_app_state().download_manager.cancel_download(
            next(t for t in get_app_state().download_manager.download_tasks if t.id == task_id)
        )

    def _on_retry_clicked(self, task_id: str):
        log.info("Retry requested for task: %s", task_id)
