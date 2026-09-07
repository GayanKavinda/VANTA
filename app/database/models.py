import time
from enum import Enum as PyEnum

from sqlalchemy import Column, String, Integer, Float, DateTime, Text

from app.database.connection import Base


class DownloadStatus(str, PyEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRecord(Base):
    __tablename__ = "downloads"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    source_url = Column(String, nullable=False)
    download_url = Column(String)
    destination = Column(String)
    status = Column(String, default=DownloadStatus.QUEUED.value)
    total_size = Column(Integer, default=0)
    downloaded_size = Column(Integer, default=0)
    speed = Column(Float, default=0.0)
    progress = Column(Float, default=0.0)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)
    error = Column(Text, nullable=True)
    error_type = Column(String, nullable=True)
    supports_resume = Column(Integer, default=False)
    queue_order = Column(Integer, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "source_url": self.source_url,
            "download_url": self.download_url,
            "destination": self.destination,
            "status": self.status,
            "total_size": self.total_size,
            "downloaded_size": self.downloaded_size,
            "speed": self.speed,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "error_type": self.error_type,
            "supports_resume": bool(self.supports_resume),
            "queue_order": self.queue_order,
        }


class SettingRecord(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
