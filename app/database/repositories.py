import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy import text

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.connection import get_session, engine, init_db
from app.database.models import DownloadRecord, DownloadStatus
from app.utils.logger import get_logger

log = get_logger("vanta.database.repositories")


class DatabaseConsistencyIssue(str, Enum):
    """Types of consistency issues detected."""
    INVALID_STATUS = "invalid_status"
    INVALID_ERROR_TYPE = "invalid_error_type"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    INVALID_QUEUE_ORDER = "invalid_queue_order"
    NEGATIVE_SIZE = "negative_size"
    INVALID_PROGRESS = "invalid_progress"
    ORPHANED_RECORD = "orphaned_record"


@dataclass
class ValidationReport:
    """Report of database validation."""
    total_records: int = 0
    valid_records: int = 0
    issues_found: int = 0
    issues: list[tuple[str, DatabaseConsistencyIssue, str]] = None  # (record_id, issue, detail)

    def __post_init__(self):
        if self.issues is None:
            self.issues = []

    def add_issue(self, record_id: str, issue: DatabaseConsistencyIssue, detail: str):
        self.issues.append((record_id, issue.value, detail))
        self.issues_found += 1

    def summary(self) -> str:
        lines = [
            f"Database Validation Report:",
            f"  Total records: {self.total_records}",
            f"  Valid records: {self.valid_records}",
            f"  Issues found: {self.issues_found}",
        ]
        if self.issues:
            lines.append("  Issues:")
            for record_id, issue, detail in self.issues:
                lines.append(f"    {record_id[:8]}: {issue} - {detail}")
        return "\n".join(lines)


def _migrate_downloads_table():
    init_db()
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


def _validate_task(task: DownloadTask, report: ValidationReport):
    """Validate a task for consistency issues."""
    if not task.id:
        report.add_issue("unknown", DatabaseConsistencyIssue.MISSING_REQUIRED_FIELD, "Missing task ID")
        return False

    if not task.name:
        report.add_issue(task.id, DatabaseConsistencyIssue.MISSING_REQUIRED_FIELD, "Missing task name")
        return False

    if not task.destination:
        report.add_issue(task.id, DatabaseConsistencyIssue.MISSING_REQUIRED_FIELD, "Missing destination")
        return False

    if not task.download_url:
        report.add_issue(task.id, DatabaseConsistencyIssue.MISSING_REQUIRED_FIELD, "Missing download URL")
        return False

    try:
        TaskStatus(task.status)
    except ValueError:
        report.add_issue(task.id, DatabaseConsistencyIssue.INVALID_STATUS, f"Invalid status: {task.status}")
        return False

    if task.error_type is not None:
        try:
            DownloadErrorType(task.error_type)
        except ValueError:
            report.add_issue(task.id, DatabaseConsistencyIssue.INVALID_ERROR_TYPE, f"Invalid error_type: {task.error_type}")
            return False

    if task.queue_order is not None and task.queue_order < 0:
        report.add_issue(task.id, DatabaseConsistencyIssue.INVALID_QUEUE_ORDER, f"Negative queue_order: {task.queue_order}")
        return False

    if task.total_size is not None and task.total_size < 0:
        report.add_issue(task.id, DatabaseConsistencyIssue.NEGATIVE_SIZE, f"Negative total_size: {task.total_size}")
        return False

    if task.downloaded_size is not None and task.downloaded_size < 0:
        report.add_issue(task.id, DatabaseConsistencyIssue.NEGATIVE_SIZE, f"Negative downloaded_size: {task.downloaded_size}")
        return False

    if task.progress is not None and (task.progress < 0 or task.progress > 100):
        report.add_issue(task.id, DatabaseConsistencyIssue.INVALID_PROGRESS, f"Invalid progress: {task.progress}")
        return False

    return True


def validate_database() -> ValidationReport:
    """Validate all records in the database for consistency issues."""
    _migrate_downloads_table()
    session = get_session()
    report = ValidationReport()
    try:
        records = session.query(DownloadRecord).all()
        report.total_records = len(records)
        for r in records:
            # Validate raw status BEFORE normalizing
            try:
                TaskStatus(r.status)
            except ValueError:
                report.add_issue(r.id, DatabaseConsistencyIssue.INVALID_STATUS, f"Invalid status: {r.status}")
                # Still normalize for task creation
                status = TaskStatus.QUEUED
            else:
                status = TaskStatus(r.status)

            try:
                error_type = (
                    DownloadErrorType(r.error_type)
                    if r.error_type
                    else DownloadErrorType.UNKNOWN
                )
            except ValueError:
                error_type = DownloadErrorType.UNKNOWN

            task = DownloadTask(
                id=r.id,
                name=r.name,
                source_url=r.source_url,
                download_url=r.download_url or "",
                destination=r.destination or "",
                status=status,
                total_size=r.total_size,
                downloaded_size=r.downloaded_size,
                speed=r.speed,
                progress=r.progress,
                created_at=r.created_at,
                updated_at=r.updated_at,
                error=r.error,
                error_type=error_type,
                supports_resume=bool(r.supports_resume),
                queue_order=r.queue_order or 0,
            )

            if _validate_task(task, report):
                report.valid_records += 1
        log.info(report.summary())
        return report
    finally:
        session.close()


def fix_database_issues() -> ValidationReport:
    """Fix common consistency issues in the database."""
    _migrate_downloads_table()
    session = get_session()
    report = ValidationReport()
    try:
        records = session.query(DownloadRecord).all()
        report.total_records = len(records)
        fixed_count = 0

        for r in records:
            needs_update = False
            task_id = r.id

            # Fix invalid status
            try:
                TaskStatus(r.status)
            except ValueError:
                report.add_issue(task_id, DatabaseConsistencyIssue.INVALID_STATUS, f"Fixed invalid status '{r.status}' to QUEUED")
                r.status = TaskStatus.QUEUED.value
                needs_update = True

            # Fix invalid error_type
            if r.error_type:
                try:
                    DownloadErrorType(r.error_type)
                except ValueError:
                    report.add_issue(task_id, DatabaseConsistencyIssue.INVALID_ERROR_TYPE, f"Fixed invalid error_type '{r.error_type}' to UNKNOWN")
                    r.error_type = DownloadErrorType.UNKNOWN.value
                    needs_update = True

            # Fix negative queue_order
            if r.queue_order is not None and r.queue_order < 0:
                report.add_issue(task_id, DatabaseConsistencyIssue.INVALID_QUEUE_ORDER, f"Fixed negative queue_order {r.queue_order} to 0")
                r.queue_order = 0
                needs_update = True

            # Fix negative sizes
            if r.total_size is not None and r.total_size < 0:
                report.add_issue(task_id, DatabaseConsistencyIssue.NEGATIVE_SIZE, f"Fixed negative total_size {r.total_size} to 0")
                r.total_size = 0
                needs_update = True

            if r.downloaded_size is not None and r.downloaded_size < 0:
                report.add_issue(task_id, DatabaseConsistencyIssue.NEGATIVE_SIZE, f"Fixed negative downloaded_size {r.downloaded_size} to 0")
                r.downloaded_size = 0
                needs_update = True

            # Fix invalid progress
            if r.progress is not None and (r.progress < 0 or r.progress > 100):
                report.add_issue(task_id, DatabaseConsistencyIssue.INVALID_PROGRESS, f"Fixed invalid progress {r.progress} to 0")
                r.progress = 0.0
                needs_update = True

            if needs_update:
                r.updated_at = time.time()
                fixed_count += 1

        session.commit()
        log.info(f"Fixed {fixed_count} database consistency issues")
        log.info(report.summary())
        return report
    finally:
        session.close()


def save_download_task(task: DownloadTask):
    _migrate_downloads_table()
    
    # Validate before saving
    report = ValidationReport()
    if not _validate_task(task, report):
        log.warning("Attempted to save invalid task %s: %s", task.id, report.summary())
        # Still save but with warning
    
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
            try:
                status = TaskStatus(r.status)
            except ValueError:
                status = TaskStatus.QUEUED

            try:
                error_type = (
                    DownloadErrorType(r.error_type)
                    if r.error_type
                    else DownloadErrorType.UNKNOWN
                )
            except ValueError:
                error_type = DownloadErrorType.UNKNOWN

            task = DownloadTask(
                id=r.id,
                name=r.name,
                source_url=r.source_url,
                download_url=r.download_url or "",
                destination=r.destination or "",
                status=status,
                total_size=r.total_size,
                downloaded_size=r.downloaded_size,
                speed=r.speed,
                progress=r.progress,
                created_at=r.created_at,
                updated_at=r.updated_at,
                error=r.error,
                error_type=error_type,
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
