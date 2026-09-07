import time
from typing import Optional

from sqlalchemy import text

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.connection import get_session, engine
from app.database.models import DownloadRecord, DownloadStatus
from app.utils.logger import get_logger

log = get_logger("vanta.database.repositories")


def _migrate_downloads_table():
    with engine.connect() as conn:
        existing_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(downloads)")).fetchall()
        }
        pending = []
        if "error_type" not in existing_columns:
            pending.append("ALTER TABLE downloads ADD COLUMN error_type TEXT")
        if "queue_order" not in existing_columns:
            pending.append("ALTER TABLE downloads ADD COLUMN queue_order INTEGER")
        for stmt in pending:
            conn.execute(text(stmt))
            conn.commit()


def save_download_task(task: DownloadTask):
    _migrate_downloads_table()
    session = get_session()
    try:
        record = session.get(DownloadRecord, task.id)
        if record:
            record.name = task.name
            record.source_url = task.source_url
            record.download_url = task.download_url
            record.destination = task.destination
            record.status = task.status.value
            record.total_size = task.total_size
            record.downloaded_size = task.downloaded_size
            record.speed = task.speed
            record.progress = task.progress
            record.updated_at = time.time()
            record.error = task.error
            record.error_type = task.error_type.value if task.error_type else None
            record.supports_resume = int(task.supports_resume)
            record.queue_order = task.queue_order or None
        else:
            record = DownloadRecord(
                id=task.id,
                name=task.name,
                source_url=task.source_url,
                download_url=task.download_url,
                destination=task.destination,
                status=task.status.value,
                total_size=task.total_size,
                downloaded_size=task.downloaded_size,
                speed=task.speed,
                progress=task.progress,
                created_at=task.created_at,
                updated_at=task.updated_at,
                error=task.error,
                error_type=task.error_type.value if task.error_type else None,
                supports_resume=int(task.supports_resume),
                queue_order=task.queue_order or None,
            )
            session.add(record)
        session.commit()
    finally:
        session.close()


def load_download_tasks() -> list[DownloadTask]:
    _migrate_downloads_table()
    session = get_session()
    try:
        records = session.query(DownloadRecord).all()
        tasks = []
        for r in records:
            task = DownloadTask(
                id=r.id,
                name=r.name,
                source_url=r.source_url,
                download_url=r.download_url or "",
                destination=r.destination or "",
                status=TaskStatus(r.status),
                total_size=r.total_size,
                downloaded_size=r.downloaded_size,
                speed=r.speed,
                progress=r.progress,
                created_at=r.created_at,
                updated_at=r.updated_at,
                error=r.error,
                error_type=DownloadErrorType(r.error_type) if r.error_type else DownloadErrorType.UNKNOWN,
                supports_resume=bool(r.supports_resume),
                queue_order=r.queue_order or 0,
            )
            tasks.append(task)
        return tasks
    finally:
        session.close()


def delete_download_task(task_id: str):
    session = get_session()
    try:
        record = session.get(DownloadRecord, task_id)
        if record:
            session.delete(record)
            session.commit()
    finally:
        session.close()


def clear_download_history():
    session = get_session()
    try:
        session.query(DownloadRecord).delete()
        session.commit()
    finally:
        session.close()
