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
    remove_requested = Signal(str)

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

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setFixedSize(90, 32)
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self._task.id))

        layout.addLayout(self._button_row)

    def update_from_task(self, task: DownloadTask):
        self._task = task

        self._name_label.setText(task.name)
        self._status_label.setText(self.STATUS_LABELS.get(task.status, task.status.value))
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

        if task.status == TaskStatus.QUEUED and task.queue_position > 0:
            self._status_label.setText(f"Queued · #{task.queue_position}")

        for button in (
            self._pause_btn,
            self._resume_btn,
            self._cancel_btn,
            self._retry_btn,
            self._open_btn,
            self._remove_btn,
        ):
            self._button_row.removeWidget(button)

        if task.status == TaskStatus.QUEUED:
            self._button_row.addWidget(self._cancel_btn)
        elif task.status == TaskStatus.DOWNLOADING:
            self._button_row.addWidget(self._pause_btn)
            self._button_row.addWidget(self._cancel_btn)
        elif task.status == TaskStatus.PAUSED:
            self._button_row.addWidget(self._resume_btn)
            self._button_row.addWidget(self._cancel_btn)
        elif task.status in (TaskStatus.PREPARING, TaskStatus.VERIFYING):
            self._button_row.addWidget(self._cancel_btn)
        elif task.status == TaskStatus.FAILED:
            self._button_row.addWidget(self._retry_btn)
            self._button_row.addWidget(self._cancel_btn)
        elif task.status == TaskStatus.COMPLETED:
            self._button_row.addWidget(self._open_btn)
            self._button_row.addWidget(self._remove_btn)

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
