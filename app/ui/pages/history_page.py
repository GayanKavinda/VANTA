import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.core.task_manager import DownloadTask, TaskStatus
from app.core.queue_controller import QueueController
from app.database.repositories import load_history_tasks, delete_download_task
from app.services.filename_service import format_file_size
from app.utils.logger import get_logger

log = get_logger("vanta.ui.history")


def _fmt_date(ts: float) -> str:
    t = time.localtime(ts)
    if time.localtime().tm_yday == t.tm_yday and time.localtime().tm_year == t.tm_year:
        return "Today"
    yesterday = time.time() - 86400
    if time.localtime(yesterday).tm_yday == t.tm_yday and time.localtime(yesterday).tm_year == t.tm_year:
        return "Yesterday"
    if abs(time.time() - ts) < 86400 * 7:
        return time.strftime("%A", t)
    return time.strftime("%b %d, %Y", t)


class HistoryPage(QWidget):
    def __init__(self):
        super().__init__()

        self._tasks: list[DownloadTask] = []
        self._cards: dict[str, HistoryCard] = {}
        self._queue_controller: QueueController | None = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(48, 48, 48, 48)
        main_layout.setSpacing(16)

        title = QLabel("History")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        main_layout.addWidget(title)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search downloads...")
        self._search_input.setFixedHeight(36)
        self._search_input.textChanged.connect(self._on_search)
        main_layout.addWidget(self._search_input)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 13px; color: #8A8A9A;")
        main_layout.addWidget(self._status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignTop)

        scroll.setWidget(self._content_widget)
        main_layout.addWidget(scroll, stretch=1)

        self._load_history()

    def set_queue_controller(self, controller: QueueController | None):
        """Subscribe to queue changes so History updates live when a
        download reaches a terminal state.

        Uses the existing public ``add_queue_callback`` API — no private
        manager or scheduler state is touched.
        """
        if self._queue_controller is not None:
            self._queue_controller.remove_queue_callback(self._on_queue_changed)

        self._queue_controller = controller
        if controller is not None:
            controller.add_queue_callback(self._on_queue_changed)

    def _on_queue_changed(self, task: DownloadTask | None):
        """Refresh history when a task becomes terminal or a bulk change
        occurs.  Only terminal / bulk events trigger a reload — progress
        updates on in-flight downloads do not.
        """
        if task is not None and not task.is_terminal:
            if task.id in self._cards:
                self._tasks = [t for t in self._tasks if t.id != task.id]
                self._render(self._tasks)
            return
        QTimer.singleShot(0, self._load_history)

    def _load_history(self):
        self._tasks = load_history_tasks()

        self._status_label.setText(f"{len(self._tasks)} items")
        self._render(self._tasks)

    def _on_search(self, text: str):
        if not text:
            self._render(self._tasks)
            return

        needle = text.lower()
        filtered = [
            t for t in self._tasks
            if needle in t.name.lower()
            or needle in (t.source_url or "").lower()
            or needle in (t.destination or "").lower()
            or needle in t.status.value.lower()
        ]
        self._render(filtered)

    def _render(self, tasks: list[DownloadTask]):
        self._cards.clear()
        for i in reversed(range(self._content_layout.count())):
            widget = self._content_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        if not tasks:
            placeholder = QLabel("No download history")
            placeholder.setStyleSheet("color: #8A8A9A; padding: 20px;")
            self._content_layout.addWidget(placeholder)
            return

        self._status_label.setText(f"{len(self._tasks)} items")

        current_date = None
        for task in tasks:
            date_group = _fmt_date(task.updated_at)
            if date_group != current_date:
                current_date = date_group
                date_label = QLabel(date_group)
                date_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8A8A9A; padding-top: 8px;")
                self._content_layout.addWidget(date_label)

            card = HistoryCard(task)
            card.open_file_requested.connect(self._on_open_file)
            card.open_folder_requested.connect(self._on_open_folder)
            card.retry_requested.connect(self._on_retry)
            card.remove_requested.connect(self._on_remove)
            self._cards[task.id] = card
            self._content_layout.addWidget(card)

    def _resolve_task(self, task_id: str) -> DownloadTask | None:
        return next((t for t in self._tasks if t.id == task_id), None)

    def _on_open_folder(self, task_id: str):
        task = self._resolve_task(task_id)
        if task is None or not task.destination:
            return

        dest = Path(task.destination)
        target = dest.parent if dest.is_file() else dest
        if target.exists() and target.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _on_open_file(self, task_id: str):
        task = self._resolve_task(task_id)
        if task is None or not task.destination:
            return

        dest_path = Path(task.destination)
        if dest_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(dest_path)))

    def _on_retry(self, task_id: str):
        if self._queue_controller is None:
            return
        task = self._queue_controller.find_task(task_id)
        if task is not None and task.status == TaskStatus.FAILED:
            self._queue_controller.retry_download(task_id)

    def _on_remove(self, task_id: str):
        delete_download_task(task_id)
        self._tasks = [t for t in self._tasks if t.id != task_id]
        self._render(self._tasks)

    def refresh(self):
        self._load_history()


class HistoryCard(QWidget):
    open_file_requested = Signal(str)
    open_folder_requested = Signal(str)
    retry_requested = Signal(str)
    remove_requested = Signal(str)

    STATUS_ICONS = {
        TaskStatus.COMPLETED: "✓",
        TaskStatus.FAILED: "✕",
        TaskStatus.CANCELLED: "✕",
    }

    def __init__(self, task: DownloadTask):
        super().__init__()
        self._task = task

        self.setObjectName("history_card")
        self.setMinimumHeight(100)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)

        icon_layout = QVBoxLayout()
        icon_layout.setContentsMargins(0, 0, 0, 0)

        self._icon_label = QLabel(self.STATUS_ICONS.get(task.status, "?"))
        self._icon_label.setStyleSheet("font-size: 20px;")
        icon_layout.addWidget(self._icon_label)
        icon_layout.addStretch(1)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(2)

        self._name_label = QLabel(task.name)
        self._name_label.setStyleSheet("font-size: 13px; font-weight: 500;")

        self._status_label = QLabel(self._status_text(task))
        self._status_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")

        self._detail_label = QLabel(self._detail_text(task))
        self._detail_label.setStyleSheet("font-size: 11px; color: #8A8A9A;")

        self._missing_label = QLabel()
        self._missing_label.setStyleSheet("font-size: 11px; color: #FF6B6B;")
        self._missing_label.hide()

        self._error_label = QLabel()
        self._error_label.setStyleSheet("font-size: 11px; color: #FF6B6B;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()

        content.addWidget(self._name_label)
        content.addWidget(self._status_label)
        content.addWidget(self._detail_label)
        content.addWidget(self._missing_label)
        content.addWidget(self._error_label)
        content.addStretch(1)

        btn_layout = QVBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)

        self._open_file_btn = QPushButton("Open File")
        self._open_file_btn.setFixedSize(100, 28)
        self._open_file_btn.setStyleSheet(self._btn_style())
        self._open_file_btn.clicked.connect(lambda: self.open_file_requested.emit(self._task.id))
        btn_layout.addWidget(self._open_file_btn, alignment=Qt.AlignRight)

        self._open_folder_btn = QPushButton("Open Folder")
        self._open_folder_btn.setFixedSize(100, 28)
        self._open_folder_btn.setStyleSheet(self._btn_style())
        self._open_folder_btn.clicked.connect(lambda: self.open_folder_requested.emit(self._task.id))
        btn_layout.addWidget(self._open_folder_btn, alignment=Qt.AlignRight)

        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setFixedSize(100, 28)
        self._retry_btn.setStyleSheet(self._btn_style())
        self._retry_btn.clicked.connect(lambda: self.retry_requested.emit(self._task.id))
        btn_layout.addWidget(self._retry_btn, alignment=Qt.AlignRight)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setFixedSize(100, 28)
        self._remove_btn.setStyleSheet(self._btn_style())
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._task.id))
        btn_layout.addWidget(self._remove_btn, alignment=Qt.AlignRight)

        layout.addLayout(icon_layout)
        layout.addLayout(content, stretch=1)
        layout.addLayout(btn_layout)

        self.update_from_task(task)

    @staticmethod
    def _btn_style() -> str:
        return """
            QPushButton {
                background: transparent;
                border: 1px solid #292B33;
                border-radius: 6px;
                color: #E8E8EA;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #292B33;
            }
        """

    def _status_text(self, task: DownloadTask) -> str:
        return task.status.value.title()

    def _detail_text(self, task: DownloadTask) -> str:
        parts = []
        if task.total_size > 0:
            parts.append(format_file_size(task.total_size))
        elif task.downloaded_size > 0:
            parts.append(format_file_size(task.downloaded_size))
        else:
            parts.append("Unknown size")

        if task.destination:
            parent = Path(task.destination).parent
            parts.append(str(parent))

        return " · ".join(parts)

    def update_from_task(self, task: DownloadTask):
        self._task = task

        self._icon_label.setText(self.STATUS_ICONS.get(task.status, "?"))
        self._name_label.setText(task.name)
        self._status_label.setText(self._status_text(task))
        self._detail_label.setText(self._detail_text(task))

        if task.status == TaskStatus.FAILED and task.error:
            self._error_label.setText(task.error)
            self._error_label.show()
        else:
            self._error_label.hide()

        # File existence check for COMPLETED tasks.
        file_exists = False
        if task.destination:
            dest_path = Path(task.destination)
            file_exists = dest_path.is_file()

        if task.status == TaskStatus.COMPLETED:
            if file_exists:
                self._missing_label.hide()
                self._open_file_btn.show()
            else:
                self._missing_label.setText("File not found")
                self._missing_label.show()
                self._open_file_btn.hide()
        else:
            self._missing_label.hide()
            self._open_file_btn.hide()

        # Open Folder available whenever destination exists (parent dir).
        self._open_folder_btn.setVisible(bool(task.destination))

        # Retry only for FAILED tasks.
        self._retry_btn.setVisible(task.status == TaskStatus.FAILED)
