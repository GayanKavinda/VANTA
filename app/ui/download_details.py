"""V2.0 Phase 2 — Download details dialog.

Shows information already available from the task/resource model.
Does not modify task state simply by being opened.
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.services.filename_service import file_category_label, format_file_size
from app.utils.logger import get_logger

log = get_logger("vanta.ui.download_details")


def _format_timestamp(ts: float | None) -> str:
    if not ts:
        return "Unknown"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "Unknown"


def _status_label(status: TaskStatus) -> str:
    labels = {
        TaskStatus.QUEUED: "Queued",
        TaskStatus.PREPARING: "Preparing",
        TaskStatus.DOWNLOADING: "Downloading",
        TaskStatus.PAUSED: "Paused",
        TaskStatus.VERIFYING: "Verifying",
        TaskStatus.COMPLETED: "Completed",
        TaskStatus.FAILED: "Failed",
        TaskStatus.CANCELLED: "Cancelled",
    }
    return labels.get(status, status.value)


class DownloadDetailsDialog(QDialog):
    """Lightweight details view for a single download task."""

    def __init__(self, task: DownloadTask, parent: QWidget | None = None):
        super().__init__(parent)
        self._task = task
        self.setWindowTitle("Download Details")
        self.setMinimumSize(480, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Download Details")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setSpacing(12)

        form.addRow("Filename:", QLabel(task.name))
        form.addRow("Status:", QLabel(_status_label(task.status)))
        form.addRow("Source URL:", self._make_link_label(task.source_url))
        form.addRow("Download URL:", self._make_link_label(task.download_url))
        form.addRow("Destination:", QLabel(str(task.destination)))

        size_text = format_file_size(task.total_size) if task.total_size else "Unknown"
        form.addRow("Total size:", QLabel(size_text))

        downloaded_text = format_file_size(task.downloaded_size) if task.downloaded_size else "0 B"
        form.addRow("Downloaded:", QLabel(downloaded_text))

        form.addRow("Progress:", QLabel(f"{task.progress:.1f}%"))
        form.addRow("Speed:", QLabel(task.format_speed()))

        resume_text = "Yes" if task.supports_resume else "No"
        form.addRow("Supports resume:", QLabel(resume_text))

        form.addRow("Created:", QLabel(_format_timestamp(task.created_at)))
        form.addRow("Updated:", QLabel(_format_timestamp(task.updated_at)))

        if task.error:
            error_label = QLabel(task.error)
            error_label.setWordWrap(True)
            error_label.setStyleSheet("color: #FF6B6B; font-size: 12px;")
            form.addRow("Error:", error_label)

        if task.error_type and task.error_type != DownloadErrorType.UNKNOWN:
            form.addRow("Error type:", QLabel(task.error_type.value))

        layout.addLayout(form)
        layout.addStretch(1)

        button_box = QDialogButtonBox()
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        button_box.addButton(close_btn, QDialogButtonBox.RejectRole)
        layout.addWidget(button_box)

    @staticmethod
    def _make_link_label(url: str) -> QLabel:
        label = QLabel(url)
        label.setWordWrap(True)
        label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        return label


__all__ = ["DownloadDetailsDialog"]