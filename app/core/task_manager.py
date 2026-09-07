import enum
import time
from dataclasses import dataclass, field
from typing import Optional


class TaskStatus(enum.Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadErrorType(enum.Enum):
    NETWORK = "network"
    ACCESS_DENIED = "access_denied"
    HTML_RESPONSE = "html_response"
    RANGE_UNSUPPORTED = "range_unsupported"
    DISK = "disk"
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


@dataclass
class DownloadTask:
    id: str
    name: str
    source_url: str
    download_url: str
    destination: str
    status: TaskStatus = TaskStatus.QUEUED
    total_size: int = 0
    downloaded_size: int = 0
    speed: float = 0.0
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    error_type: DownloadErrorType = DownloadErrorType.UNKNOWN
    supports_resume: bool = False
    queue_order: int = 0

    @property
    def is_active(self) -> bool:
        return self.status in (
            TaskStatus.DOWNLOADING,
            TaskStatus.PREPARING,
            TaskStatus.VERIFYING,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )

    @property
    def queue_position(self) -> int:
        return self._queue_position

    @queue_position.setter
    def queue_position(self, value: int):
        self._queue_position = value

    def __post_init__(self):
        self._queue_position = 0

    def update_progress(self, downloaded: int, total: int, speed: float):
        self.downloaded_size = downloaded
        if total > 0:
            self.total_size = total
            self.progress = round((downloaded / total) * 100, 1)
        self.speed = speed
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "source_url": self.source_url,
            "download_url": self.download_url,
            "destination": self.destination,
            "status": self.status.value,
            "total_size": self.total_size,
            "downloaded_size": self.downloaded_size,
            "speed": self.speed,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "error_type": self.error_type.value,
            "supports_resume": self.supports_resume,
            "queue_position": self.queue_position,
            "queue_order": self.queue_order,
        }
