import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from app.core.task_manager import DownloadTask, TaskStatus


class DownloadCard(QFrame):
    pause_requested = Signal(str)
    resume_requested = Signal(str)
    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    open_requested = Signal(str)
    open_file_requested = Signal(str)
    remove_requested = Signal(str)
    move_up_requested = Signal(str)
    move_down_requested = Signal(str)
    move_top_requested = Signal(str)
    move_bottom_requested = Signal(str)
    details_requested = Signal(str)

    STATUS_LABELS = {
        TaskStatus.QUEUED: "Queued",
        TaskStatus.PREPARING: "Preparing",
        TaskStatus.DOWNLOADING: "Downloading",
        TaskStatus.PAUSED: "Paused",
        TaskStatus.VERIFYING: "Verifying",
        TaskStatus.COMPLETED: "Completed",
        TaskStatus.FAILED: "Failed",
        TaskStatus.CANCELLED: "Cancelled",
    }

    def __init__(self, task: DownloadTask):
        super().__init__()
        self._task = task
        self._setup_ui()
        self.update_from_task(task)

    def _setup_ui(self):
        self.setObjectName("download_card")
        self.setMinimumHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        top_row.addWidget(self._name_label, stretch=1)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        top_row.addWidget(self._status_label)

        layout.addLayout(top_row)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        layout.addWidget(self._progress_bar)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(8)

        self._size_label = QLabel()
        self._size_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        detail_row.addWidget(self._size_label)

        self._speed_label = QLabel()
        self._speed_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        detail_row.addWidget(self._speed_label)

        self._eta_label = QLabel()
        self._eta_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        detail_row.addWidget(self._eta_label)

        detail_row.addStretch(1)
        layout.addLayout(detail_row)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("font-size: 12px; color: #FF6B6B;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self._queue_action_row = QHBoxLayout()
        self._queue_action_row.setSpacing(8)
        self._queue_action_row.addStretch(1)

        self._move_up_btn = QPushButton("Move Up")
        self._move_up_btn.setFixedSize(80, 28)
        self._move_up_btn.clicked.connect(lambda: self.move_up_requested.emit(self._task.id))
        self._queue_action_row.addWidget(self._move_up_btn)

        self._move_down_btn = QPushButton("Move Down")
        self._move_down_btn.setFixedSize(80, 28)
        self._move_down_btn.clicked.connect(lambda: self.move_down_requested.emit(self._task.id))
        self._queue_action_row.addWidget(self._move_down_btn)

        self._move_top_btn = QPushButton("Move to Top")
        self._move_top_btn.setFixedSize(90, 28)
        self._move_top_btn.clicked.connect(lambda: self.move_top_requested.emit(self._task.id))
        self._queue_action_row.addWidget(self._move_top_btn)

        self._move_bottom_btn = QPushButton("Move to Bottom")
        self._move_bottom_btn.setFixedSize(100, 28)
        self._move_bottom_btn.clicked.connect(lambda: self.move_bottom_requested.emit(self._task.id))
        self._queue_action_row.addWidget(self._move_bottom_btn)

        layout.addLayout(self._queue_action_row)

        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(8)
        self._button_row.addStretch(1)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.setFixedSize(90, 32)
        self._pause_btn.clicked.connect(lambda: self.pause_requested.emit(self._task.id))

        self._resume_btn = QPushButton("Resume")
        self._resume_btn.setFixedSize(90, 32)
        self._resume_btn.clicked.connect(lambda: self.resume_requested.emit(self._task.id))

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedSize(90, 32)
        self._cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(self._task.id))

        self._retry_btn = QPushButton("Retry")
        self._retry_btn.setFixedSize(90, 32)
        self._retry_btn.clicked.connect(lambda: self.retry_requested.emit(self._task.id))

        self._open_btn = QPushButton("Open Folder")
        self._open_btn.setFixedSize(100, 32)
        self._open_btn.clicked.connect(lambda: self.open_requested.emit(self._task.id))

        self._open_file_btn = QPushButton("Open File")
        self._open_file_btn.setFixedSize(100, 32)
        self._open_file_btn.clicked.connect(lambda: self.open_file_requested.emit(self._task.id))

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setFixedSize(90, 32)
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._task.id))

        self._details_btn = QPushButton("Details")
        self._details_btn.setFixedSize(90, 32)
        self._details_btn.clicked.connect(lambda: self.details_requested.emit(self._task.id))

        layout.addLayout(self._button_row)

    def update_from_task(self, task: DownloadTask):
        self._task = task

        self._name_label.setText(task.name)

        status_text = self._get_status_text(task)
        self._status_label.setText(status_text)
        self._progress_bar.setValue(int(task.progress))

        self._size_label.setText(self._format_size(task))
        self._speed_label.setText(task.format_speed())

        if task.eta_seconds is not None and task.is_downloading:
            self._eta_label.setText(f"ETA {task.format_eta()}")
            self._eta_label.show()
        else:
            self._eta_label.hide()

        if task.status == TaskStatus.FAILED and task.error:
            self._error_label.setText(task.error)
            self._error_label.show()
        else:
            self._error_label.hide()

        # Show/hide queue action buttons for queued tasks
        is_queued = task.status == TaskStatus.QUEUED
        self._move_up_btn.setVisible(is_queued)
        self._move_down_btn.setVisible(is_queued)
        self._move_top_btn.setVisible(is_queued)
        self._move_bottom_btn.setVisible(is_queued)

        for button in (
            self._pause_btn,
            self._resume_btn,
            self._cancel_btn,
            self._retry_btn,
            self._open_btn,
            self._open_file_btn,
            self._remove_btn,
        ):
            self._button_row.removeWidget(button)
            button.setVisible(False)

        # Details is always available regardless of state.
        self._button_row.addWidget(self._details_btn)
        self._details_btn.setVisible(True)

        if task.status == TaskStatus.QUEUED:
            self._button_row.addWidget(self._cancel_btn)
            self._cancel_btn.setVisible(True)
        elif task.status == TaskStatus.DOWNLOADING:
            self._button_row.addWidget(self._pause_btn)
            self._button_row.addWidget(self._cancel_btn)
            self._pause_btn.setVisible(True)
            self._cancel_btn.setVisible(True)
        elif task.status == TaskStatus.PAUSED:
            self._button_row.addWidget(self._resume_btn)
            self._button_row.addWidget(self._cancel_btn)
            self._resume_btn.setVisible(True)
            self._cancel_btn.setVisible(True)
        elif task.status in (TaskStatus.PREPARING, TaskStatus.VERIFYING):
            self._button_row.addWidget(self._cancel_btn)
            self._cancel_btn.setVisible(True)
        elif task.status == TaskStatus.FAILED:
            self._button_row.addWidget(self._retry_btn)
            self._retry_btn.setVisible(True)
        elif task.status == TaskStatus.COMPLETED:
            self._button_row.addWidget(self._open_file_btn)
            self._button_row.addWidget(self._open_btn)
            self._button_row.addWidget(self._remove_btn)
            self._open_file_btn.setVisible(True)
            self._open_btn.setVisible(True)
            self._remove_btn.setVisible(True)

    def _get_status_text(self, task: DownloadTask) -> str:
        if task.status == TaskStatus.QUEUED:
            if task.scheduled_at is not None and task.scheduled_at > time.time():
                starts_at = datetime.fromtimestamp(task.scheduled_at).strftime("%b %d, %Y %H:%M")
                remaining = max(0, int(task.scheduled_at - time.time()))
                minutes, seconds = divmod(remaining, 60)
                hours, minutes = divmod(minutes, 60)
                countdown = f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"
                return f"Scheduled\nStarts at {starts_at}\nin {countdown}"
            if task.queue_position > 0:
                return f"Queued\nPosition #{task.queue_position}\nWaiting for available slot"
            return "Queued\nWaiting for available slot"
        elif task.status == TaskStatus.PAUSED:
            return "Paused\nUser paused"
        elif task.status == TaskStatus.COMPLETED:
            return "Completed\nFinished"
        elif task.status == TaskStatus.FAILED:
            return "Failed\nDownload failed"
        elif task.status == TaskStatus.CANCELLED:
            return "Cancelled\nDownload cancelled"
        else:
            return self.STATUS_LABELS.get(task.status, task.status.value)

    @staticmethod
    def _format_size(task: DownloadTask) -> str:
        def fmt(n):
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if n < 1024:
                    return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} {unit}"
                n /= 1024
            return f"{n:.1f} PB"

        if task.total_size > 0:
            return f"{fmt(float(task.downloaded_size))} / {fmt(float(task.total_size))}"
        return f"{fmt(float(task.downloaded_size))} downloaded"
