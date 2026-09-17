"""V1.15 — Crash Recovery & Startup Restoration

Handles recovery from unexpected application termination:
- Validates and recovers interrupted downloads
- Cleans up stale .part files
- Validates database records against filesystem state
- Provides structured recovery reporting
"""
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.utils.logger import get_logger

log = get_logger("vanta.services.crash_recovery")


class RecoveryAction(str, Enum):
    """Actions taken during crash recovery.

    Only values that describe *actual* recovery behavior are kept here.
    Interrupted downloads are converted to PAUSED (manual resume),
    never automatically resumed or restarted.
    """
    PAUSED_INTERRUPTED = "paused_interrupted"
    REMOVED_STALE_PART = "removed_stale_part"
    FIXED_INCONSISTENT_RECORD = "fixed_inconsistent_record"
    REMOVED_CORRUPTED_RECORD = "removed_corrupted_record"
    VALIDATED_PART_FILE = "validated_part_file"


class TaskValidationResult(str, Enum):
    """Result of task validation during recovery."""
    VALID = "valid"
    INVALID_MISSING_PART = "invalid_missing_part"
    INVALID_SIZE_MISMATCH = "invalid_size_mismatch"
    INVALID_ZERO_SIZE = "invalid_zero_size"
    INVALID_UNREADABLE = "invalid_unreadable"
    INVALID_DEST_EXISTS = "invalid_dest_exists"
    INVALID_STATUS_MISMATCH = "invalid_status_mismatch"
    CORRUPTED_RECORD = "corrupted_record"


@dataclass
class RecoveryReport:
    """Report of crash recovery actions."""
    total_tasks: int = 0
    queued_restored: int = 0
    paused_restored: int = 0
    interrupted_restarted: int = 0
    stale_parts_removed: int = 0
    inconsistent_records_fixed: int = 0
    corrupted_records_removed: int = 0
    actions: list[tuple[str, str, str]] = field(default_factory=list)  # (task_id, action, detail)

    def add_action(self, task_id: str, action: RecoveryAction, detail: str = ""):
        self.actions.append((task_id, action.value, detail))

    def summary(self) -> str:
        lines = [
            f"Crash Recovery Report:",
            f"  Total tasks processed: {self.total_tasks}",
            f"  Queued restored: {self.queued_restored}",
            f"  Paused restored: {self.paused_restored}",
            f"  Interrupted restarted: {self.interrupted_restarted}",
            f"  Stale .part files removed: {self.stale_parts_removed}",
            f"  Inconsistent records fixed: {self.inconsistent_records_fixed}",
            f"  Corrupted records removed: {self.corrupted_records_removed}",
        ]
        if self.actions:
            lines.append("  Actions:")
            for task_id, action, detail in self.actions:
                lines.append(f"    {task_id[:8]}: {action} {detail}")
        return "\n".join(lines)


@dataclass
class TaskValidationReport:
    """Report of a single task validation."""
    task_id: str
    result: TaskValidationResult
    part_path: Optional[Path] = None
    expected_size: int = 0
    actual_size: int = 0
    detail: str = ""


class CrashRecovery:
    """Handles crash recovery and startup restoration."""

    def __init__(self, downloads_dir: Path):
        self._downloads_dir = Path(downloads_dir)
        self._downloads_dir.mkdir(parents=True, exist_ok=True)

    def recover(self, tasks: list[DownloadTask]) -> tuple[list[DownloadTask], RecoveryReport]:
        """Run full crash recovery on loaded tasks.

        Returns:
            Tuple of (recovered_tasks, recovery_report)
        """
        report = RecoveryReport()
        report.total_tasks = len(tasks)

        # Phase 1: Validate all tasks against filesystem
        validated_tasks = []
        for task in tasks:
            validation = self._validate_task(task)
            if validation.result == TaskValidationResult.VALID:
                validated_tasks.append(task)
            else:
                fixed_task = self._handle_invalid_task(task, validation, report)
                if fixed_task:
                    validated_tasks.append(fixed_task)

        # Phase 2: Find and clean up orphaned .part files
        self._cleanup_orphaned_part_files(validated_tasks, report)

        # Phase 3: Categorize and recover tasks
        recovered = self._categorize_and_recover(validated_tasks, report)

        log.info(report.summary())
        return recovered, report

    def _validate_task(self, task: DownloadTask) -> TaskValidationReport:
        """Validate a task against filesystem state."""
        task_id = task.id
        dest_path = Path(task.destination) if task.destination else None
        part_path = dest_path.with_suffix(dest_path.suffix + ".part") if dest_path else None

        # Check for corrupted record (missing essential fields)
        if not task.id or not task.destination or not task.download_url:
            return TaskValidationReport(
                task_id=task_id,
                result=TaskValidationResult.CORRUPTED_RECORD,
                detail="Missing essential fields (id, destination, or download_url)",
            )

        # Terminal tasks don't need validation
        if task.is_terminal:
            # But check if .part file was left behind
            if part_path and part_path.exists():
                return TaskValidationReport(
                    task_id=task_id,
                    result=TaskValidationResult.INVALID_MISSING_PART,
                    part_path=part_path,
                    detail=f"Terminal task has leftover .part file ({part_path.stat().st_size} bytes)",
                )
            return TaskValidationReport(task_id=task_id, result=TaskValidationResult.VALID)

        # QUEUED tasks don't have .part files yet - they haven't started
        if task.status == TaskStatus.QUEUED:
            # But check if they claim downloaded data without .part file
            if task.downloaded_size > 0:
                return TaskValidationReport(
                    task_id=task_id,
                    result=TaskValidationResult.INVALID_MISSING_PART,
                    detail=f"Queued task claims {task.downloaded_size} bytes downloaded but no .part file exists",
                )
            return TaskValidationReport(task_id=task_id, result=TaskValidationResult.VALID)

        # PAUSED tasks also don't need .part files unless they have downloaded data
        if task.status == TaskStatus.PAUSED:
            if task.downloaded_size > 0 and (not part_path or not part_path.exists()):
                return TaskValidationReport(
                    task_id=task_id,
                    result=TaskValidationResult.INVALID_MISSING_PART,
                    detail=f"Paused task claims {task.downloaded_size} bytes downloaded but no .part file exists",
                )
            return TaskValidationReport(task_id=task_id, result=TaskValidationResult.VALID)

        # Validate .part file for active/interrupted tasks (DOWNLOADING, PREPARING, VERIFYING)
        if part_path:
            if not part_path.exists():
                return TaskValidationReport(
                    task_id=task_id,
                    result=TaskValidationResult.INVALID_MISSING_PART,
                    part_path=part_path,
                    detail="No .part file found for interrupted download",
                )

            try:
                actual_size = part_path.stat().st_size
                expected_size = task.downloaded_size

                if actual_size == 0:
                    return TaskValidationReport(
                        task_id=task_id,
                        result=TaskValidationResult.INVALID_ZERO_SIZE,
                        part_path=part_path,
                        expected_size=expected_size,
                        actual_size=0,
                        detail="Zero-byte .part file",
                    )

                if expected_size > 0 and actual_size != expected_size:
                    return TaskValidationReport(
                        task_id=task_id,
                        result=TaskValidationResult.INVALID_SIZE_MISMATCH,
                        part_path=part_path,
                        expected_size=expected_size,
                        actual_size=actual_size,
                        detail=f"Size mismatch: expected {expected_size}, found {actual_size}",
                    )

                # Try to read a byte to verify readability
                try:
                    with open(part_path, "rb") as f:
                        f.read(1)
                except (OSError, IOError) as e:
                    return TaskValidationReport(
                        task_id=task_id,
                        result=TaskValidationResult.INVALID_UNREADABLE,
                        part_path=part_path,
                        expected_size=expected_size,
                        actual_size=actual_size,
                        detail=f"Cannot read .part file: {e}",
                    )

            except (OSError, IOError) as e:
                return TaskValidationReport(
                    task_id=task_id,
                    result=TaskValidationResult.INVALID_UNREADABLE,
                    part_path=part_path,
                    detail=f"Cannot stat .part file: {e}",
                )

        else:
            # No .part file but task claims to have downloaded data
            if task.downloaded_size > 0:
                return TaskValidationReport(
                    task_id=task_id,
                    result=TaskValidationResult.INVALID_MISSING_PART,
                    detail=f"Task claims {task.downloaded_size} bytes downloaded but no .part file exists",
                )

        # Check if destination already exists (shouldn't for non-terminal)
        if dest_path and dest_path.exists() and dest_path.stat().st_size > 0:
            return TaskValidationReport(
                task_id=task_id,
                result=TaskValidationResult.INVALID_DEST_EXISTS,
                detail=f"Destination file already exists ({dest_path.stat().st_size} bytes)",
            )

        return TaskValidationReport(task_id=task_id, result=TaskValidationResult.VALID)

    def _handle_invalid_task(
        self,
        task: DownloadTask,
        validation: TaskValidationReport,
        report: RecoveryReport,
    ) -> Optional[DownloadTask]:
        """Handle an invalid task based on validation result."""
        task_id = task.id

        if validation.result == TaskValidationResult.CORRUPTED_RECORD:
            log.warning("Removing corrupted task record: %s (%s)", task_id, validation.detail)
            report.add_action(task_id, RecoveryAction.REMOVED_CORRUPTED_RECORD, validation.detail)
            report.corrupted_records_removed += 1
            return None

        if validation.result == TaskValidationResult.INVALID_DEST_EXISTS:
            log.warning("Task %s has existing destination, marking as FAILED", task_id)
            task.status = TaskStatus.FAILED
            task.error = "Destination file already exists"
            task.error_type = DownloadErrorType.EXISTING_FILE
            report.add_action(task_id, RecoveryAction.FIXED_INCONSISTENT_RECORD, validation.detail)
            report.inconsistent_records_fixed += 1
            return task

        # For .part file issues, remove the stale .part file
        if validation.part_path and validation.part_path.exists():
            try:
                size = validation.part_path.stat().st_size
                validation.part_path.unlink()
                log.warning("Removed stale .part file for %s: %d bytes (%s)",
                           task_id, size, validation.detail)
                report.add_action(task_id, RecoveryAction.REMOVED_STALE_PART, validation.detail)
                report.stale_parts_removed += 1
            except (OSError, IOError) as e:
                log.error("Failed to remove stale .part file for %s: %s", task_id, e)

        # For interrupted tasks (DOWNLOADING, PREPARING, VERIFYING), mark as PAUSED for manual resume
        if task.status in (TaskStatus.DOWNLOADING, TaskStatus.PREPARING, TaskStatus.VERIFYING):
            log.info("Interrupted task %s marked as PAUSED for manual resume", task_id)
            task.status = TaskStatus.PAUSED
            task.error = "Download was interrupted (application crash recovery)"
            task.error_type = DownloadErrorType.NETWORK
            task.supports_resume = False  # Force re-validation on resume
            report.add_action(task_id, RecoveryAction.PAUSED_INTERRUPTED, validation.detail)
            report.interrupted_restarted += 1
            return task

        # For QUEUED tasks, they're fine
        if task.status == TaskStatus.QUEUED:
            report.queued_restored += 1
            return task

        # For PAUSED tasks
        if task.status == TaskStatus.PAUSED:
            report.paused_restored += 1
            return task

        return task

    def _cleanup_orphaned_part_files(
        self,
        validated_tasks: list[DownloadTask],
        report: RecoveryReport,
    ):
        """Find and remove .part files not associated with any task."""
        task_part_files = set()
        for task in validated_tasks:
            if task.destination:
                dest_path = Path(task.destination)
                part_path = dest_path.with_suffix(dest_path.suffix + ".part")
                if part_path.exists():
                    task_part_files.add(part_path.resolve())

        # Scan downloads directory for .part files
        for part_path in self._downloads_dir.rglob("*.part"):
            if part_path.resolve() not in task_part_files:
                try:
                    size = part_path.stat().st_size
                    part_path.unlink()
                    log.warning("Removed orphaned .part file: %s (%d bytes)", part_path, size)
                    report.add_action("orphan", RecoveryAction.REMOVED_STALE_PART, str(part_path))
                    report.stale_parts_removed += 1
                except (OSError, IOError) as e:
                    log.error("Failed to remove orphaned .part file %s: %s", part_path, e)

    def _categorize_and_recover(
        self,
        tasks: list[DownloadTask],
        report: RecoveryReport,
    ) -> list[DownloadTask]:
        """Categorize tasks and apply recovery logic."""
        # Sort queued tasks by queue_order to preserve queue order
        queued_tasks = sorted(
            [t for t in tasks if t.status == TaskStatus.QUEUED],
            key=lambda t: (t.queue_order, t.created_at)
        )
        
        # Separate other task types
        paused_tasks = [t for t in tasks if t.status == TaskStatus.PAUSED]
        interrupted_tasks = [t for t in tasks if t.status in (TaskStatus.DOWNLOADING, TaskStatus.PREPARING, TaskStatus.VERIFYING)]
        terminal_tasks = [t for t in tasks if t.is_terminal]
        
        recovered = []

        # Add queued tasks in order
        for task in queued_tasks:
            task.queue_order = task.queue_order if task.queue_order > 0 else 0
            recovered.append(task)
            report.queued_restored += 1

        # Add paused tasks
        for task in paused_tasks:
            recovered.append(task)
            report.paused_restored += 1

        # Add interrupted tasks (converted to PAUSED)
        for task in interrupted_tasks:
            task.status = TaskStatus.PAUSED
            task.error = "Download was interrupted (application crash recovery)"
            task.error_type = DownloadErrorType.NETWORK
            task.supports_resume = False
            recovered.append(task)
            report.interrupted_restarted += 1

        # Add terminal tasks
        for task in terminal_tasks:
            recovered.append(task)

        return recovered

    def validate_part_file(self, part_path: Path, expected_size: int = 0) -> tuple[bool, str]:
        """Validate a .part file independently.
        
        Returns:
            Tuple of (is_valid, detail_message)
        """
        if not part_path.exists():
            return False, "File does not exist"

        try:
            actual_size = part_path.stat().st_size
        except (OSError, IOError) as e:
            return False, f"Cannot stat file: {e}"

        if actual_size == 0:
            return False, "Zero-byte file"

        if expected_size > 0 and actual_size != expected_size:
            return False, f"Size mismatch: expected {expected_size}, found {actual_size}"

        try:
            with open(part_path, "rb") as f:
                f.read(1)
        except (OSError, IOError) as e:
            return False, f"Cannot read file: {e}"

        return True, "Valid"


class StartupRestorer:
    """Orchestrates the full startup restoration sequence."""

    def __init__(self, crash_recovery: CrashRecovery):
        self._crash_recovery = crash_recovery

    def restore(self, tasks: list[DownloadTask]) -> tuple[list[DownloadTask], RecoveryReport]:
        """Run complete startup restoration."""
        log.info("Starting crash recovery for %d tasks", len(tasks))
        return self._crash_recovery.recover(tasks)

    def create_recovery_log_entry(self, report: RecoveryReport) -> str:
        """Create a structured log entry for the recovery operation."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] {report.summary()}"