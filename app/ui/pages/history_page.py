import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.core.task_manager import TaskStatus
from app.database.repositories import load_download_tasks, delete_download_task, clear_download_history
from app.database.models import DownloadRecord, DownloadStatus
from app.database.connection import get_session
from app.utils.logger import get_logger

log = get_logger("vanta.ui.history")


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


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

        self._records: list[DownloadRecord] = []

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

    def _load_history(self):
        session = get_session()
        try:
            self._records = session.query(DownloadRecord).order_by(
                DownloadRecord.created_at.desc()
            ).all()
        finally:
            session.close()

        self._status_label.setText(f"{len(self._records)} items")
        self._render(self._records)

    def _on_search(self, text: str):
        if not text:
            self._render(self._records)
            return

        filtered = [
            r for r in self._records
            if text.lower() in r.name.lower() or text.lower() in r.source_url.lower()
        ]
        self._render(filtered)

    def _render(self, records: list[DownloadRecord]):
        for i in reversed(range(self._content_layout.count())):
            widget = self._content_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        if not records:
            placeholder = QLabel("No download history")
            placeholder.setStyleSheet("color: #8A8A9A; padding: 20px;")
            self._content_layout.addWidget(placeholder)
            return

        current_date = None
        for record in records:
            date_group = _fmt_date(record.created_at)
            if date_group != current_date:
                current_date = date_group
                date_label = QLabel(date_group)
                date_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #8A8A9A; padding-top: 8px;")
                self._content_layout.addWidget(date_label)

            card = HistoryCard(record)
            card.open_requested.connect(self._on_open_folder)
            card.retry_requested.connect(self._on_retry)
            card.remove_requested.connect(self._on_remove)
            self._content_layout.addWidget(card)

    def _on_open_folder(self, record_id: str):
        record = next((r for r in self._records if r.id == record_id), None)
        if record:
            dest = Path(record.destination) if record.destination else None
            if dest and dest.exists():
                import subprocess
                subprocess.run(f'explorer /select,"{dest}"', shell=True)

    def _on_retry(self, record_id: str):
        log.info("Retry requested for download: %s", record_id)

    def _on_remove(self, record_id: str):
        delete_download_task(record_id)
        self._records = [r for r in self._records if r.id != record_id]
        self._render(self._records)
        self._status_label.setText(f"{len(self._records)} items")

    def refresh(self):
        self._load_history()


class HistoryCard(QWidget):
    open_requested = Signal(str)
    retry_requested = Signal(str)
    remove_requested = Signal(str)

    STATUS_ICONS = {
        TaskStatus.COMPLETED: "✓",
        TaskStatus.FAILED: "✕",
        TaskStatus.CANCELLED: "✕",
    }

    def __init__(self, record: DownloadRecord):
        super().__init__()
        self._record_id = record.id

        self.setObjectName("history_card")
        self.setFixedHeight(90)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(16)

        icon_layout = QVBoxLayout()
        icon_layout.setContentsMargins(0, 0, 0, 0)

        icon = QLabel(self.STATUS_ICONS.get(TaskStatus(record.status), "?"))
        icon.setStyleSheet("font-size: 20px;")
        icon_layout.addWidget(icon)
        icon_layout.addStretch(1)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(2)

        name_label = QLabel(record.name)
        name_label.setStyleSheet("font-size: 13px; font-weight: 500;")

        status_label = QLabel(TaskStatus(record.status).value.title())
        status_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")

        content.addWidget(name_label)
        content.addWidget(status_label)

        if record.error:
            error_label = QLabel(record.error)
            error_label.setStyleSheet("font-size: 11px; color: #FF6B6B;")
            content.addWidget(error_label)

        content.addStretch(1)

        btn_layout = QVBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)

        open_btn = QPushButton("Open Folder")
        open_btn.setFixedSize(100, 28)
        open_btn.setStyleSheet("""
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
        """)
        open_btn.clicked.connect(self._on_open_clicked)
        btn_layout.addWidget(open_btn, alignment=Qt.AlignRight)

        layout.addLayout(icon_layout)
        layout.addLayout(content, stretch=1)
        layout.addLayout(btn_layout)

    def _on_open_clicked(self):
        self.open_requested.emit(self._record_id)
