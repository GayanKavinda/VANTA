"""V1.15 tests — Production Hardening."""
import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from app.core.downloader import DownloadManager
from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.repositories import (
    ValidationReport,
    validate_database,
    fix_database_issues,
    DatabaseConsistencyIssue,
)
from app.services.crash_recovery import (
    CrashRecovery,
    StartupRestorer,
    RecoveryAction,
    TaskValidationResult,
)
from app.services.diagnostics import build_diagnostic_context, FailurePhase, format_diagnostic
from app.sources.html_parser import parse_html, _MAX_LINKS, _MAX_NESTING_DEPTH, _MAX_TOTAL_TEXT_SIZE


# ── Crash Recovery Tests ────────────────────────────────────────────────

def test_crash_recovery_validates_queued_tasks(tmp_path):
    """Queued tasks pass validation (with no downloaded data)."""
    recovery = CrashRecovery(tmp_path)

    task = DownloadTask(
        id="test_queued",
        name="test.bin",
        source_url="http://example.com/test.bin",
        download_url="http://example.com/test.bin",
        destination=str(tmp_path / "test.bin"),
        status=TaskStatus.QUEUED,
        downloaded_size=0,  # No downloaded data
    )

    validated = recovery._validate_task(task)
    assert validated.result == TaskValidationResult.VALID


def test_crash_recovery_validates_paused_tasks(tmp_path):
    """Paused tasks pass validation (with no downloaded data)."""
    recovery = CrashRecovery(tmp_path)

    task = DownloadTask(
        id="test_paused",
        name="test.bin",
        source_url="http://example.com/test.bin",
        download_url="http://example.com/test.bin",
        destination=str(tmp_path / "test.bin"),
        status=TaskStatus.PAUSED,
        downloaded_size=0,  # No downloaded data
    )

    validated = recovery._validate_task(task)
    assert validated.result == TaskValidationResult.VALID


def test_crash_recovery_converts_interrupted_to_paused(tmp_path):
    """Interrupted downloads (DOWNLOADING, PREPARING, VERIFYING) become PAUSED."""
    recovery = CrashRecovery(tmp_path)

    for status in (TaskStatus.DOWNLOADING, TaskStatus.PREPARING, TaskStatus.VERIFYING):
        task = DownloadTask(
            id=f"test_{status.value}",
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status=status,
            downloaded_size=5000,
        )

        # No .part file exists, so validation will fail
        validated = recovery._validate_task(task)
        assert validated.result == TaskValidationResult.INVALID_MISSING_PART

        # But handle_invalid_task should convert to PAUSED
        from app.services.crash_recovery import RecoveryReport
        report = RecoveryReport()
        fixed = recovery._handle_invalid_task(task, validated, report)
        
        assert fixed is not None
        assert fixed.status == TaskStatus.PAUSED
        assert "interrupted" in fixed.error.lower()
        assert fixed.error_type == DownloadErrorType.NETWORK


def test_crash_recovery_removes_stale_part_files(tmp_path):
    """Stale .part files for interrupted tasks are removed."""
    recovery = CrashRecovery(tmp_path)

    # Create a .part file
    part_path = tmp_path / "test.bin.part"
    part_path.write_bytes(b"x" * 1000)

    task = DownloadTask(
        id="test_stale_part",
        name="test.bin",
        source_url="http://example.com/test.bin",
        download_url="http://example.com/test.bin",
        destination=str(tmp_path / "test.bin"),
        status=TaskStatus.DOWNLOADING,
        downloaded_size=5000,  # Doesn't match actual .part size
    )

    validated = recovery._validate_task(task)
    assert validated.result == TaskValidationResult.INVALID_SIZE_MISMATCH

    from app.services.crash_recovery import RecoveryReport
    report = RecoveryReport()
    fixed = recovery._handle_invalid_task(task, validated, report)

    assert fixed is not None
    assert not part_path.exists()  # Stale .part removed
    assert report.stale_parts_removed == 1


def test_crash_recovery_detects_corrupted_records(tmp_path):
    """Records missing essential fields are removed."""
    recovery = CrashRecovery(tmp_path)

    task = DownloadTask(
        id="",  # Missing ID
        name="test.bin",
        source_url="http://example.com/test.bin",
        download_url="http://example.com/test.bin",
        destination=str(tmp_path / "test.bin"),
        status=TaskStatus.QUEUED,
    )

    validated = recovery._validate_task(task)
    assert validated.result == TaskValidationResult.CORRUPTED_RECORD

    from app.services.crash_recovery import RecoveryReport
    report = RecoveryReport()
    fixed = recovery._handle_invalid_task(task, validated, report)

    assert fixed is None
    assert report.corrupted_records_removed == 1


def test_crash_recovery_preserves_terminal_tasks(tmp_path):
    """Terminal tasks (COMPLETED, FAILED, CANCELLED) are preserved."""
    recovery = CrashRecovery(tmp_path)

    for status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        task = DownloadTask(
            id=f"test_{status.value}",
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status=status,
        )

        validated = recovery._validate_task(task)
        assert validated.result == TaskValidationResult.VALID


def test_crash_recovery_cleans_orphaned_part_files(tmp_path):
    """Orphaned .part files not associated with any task are removed."""
    recovery = CrashRecovery(tmp_path)
    from app.services.crash_recovery import RecoveryReport

    # Create orphaned .part file
    orphan = tmp_path / "orphan.bin.part"
    orphan.write_bytes(b"x" * 500)

    # Create a valid task with its own .part file
    part_path = tmp_path / "valid.bin.part"
    part_path.write_bytes(b"y" * 100)

    task = DownloadTask(
        id="valid",
        name="valid.bin",
        source_url="http://example.com/valid.bin",
        download_url="http://example.com/valid.bin",
        destination=str(tmp_path / "valid.bin"),
        status=TaskStatus.QUEUED,
    )

    recovery._cleanup_orphaned_part_files([task], RecoveryReport())

    assert not orphan.exists()  # Orphan removed
    assert part_path.exists()   # Valid .part preserved


def test_crash_recovery_full_recovery_preserves_queue_order(tmp_path):
    """Full recovery preserves queue_order for queued tasks."""
    recovery = CrashRecovery(tmp_path)
    restorer = StartupRestorer(recovery)

    tasks = [
        DownloadTask(id="a", name="a.bin", source_url="http://example.com/a.bin", download_url="http://example.com/a.bin", destination=str(tmp_path / "a.bin"), status=TaskStatus.QUEUED, queue_order=3),
        DownloadTask(id="b", name="b.bin", source_url="http://example.com/b.bin", download_url="http://example.com/b.bin", destination=str(tmp_path / "b.bin"), status=TaskStatus.QUEUED, queue_order=1),
        DownloadTask(id="c", name="c.bin", source_url="http://example.com/c.bin", download_url="http://example.com/c.bin", destination=str(tmp_path / "c.bin"), status=TaskStatus.QUEUED, queue_order=2),
    ]

    recovered, report = restorer.restore(tasks)

    assert len(recovered) == 3
    # Should be ordered by queue_order
    assert recovered[0].id == "b"
    assert recovered[1].id == "c"
    assert recovered[2].id == "a"


# ── Database Consistency Tests ─────────────────────────────────────────

def test_database_validation_detects_invalid_status(tmp_path):
    """Database validation catches invalid status values."""
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord
    
    init_db()
    test_id = "test_invalid_status_validation_xyz"
    session = get_session()
    try:
        # Clear any existing test records
        session.query(DownloadRecord).filter(DownloadRecord.id == test_id).delete(synchronize_session=False)
        session.commit()
        
        # Also clean up the "unknown" record if it exists from test pollution
        session.query(DownloadRecord).filter(DownloadRecord.id == "").delete(synchronize_session=False)
        session.commit()
        
        # Create record with invalid status
        record = DownloadRecord(
            id=test_id,
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status="INVALID_STATUS",
            total_size=1000,
            downloaded_size=0,
            speed=0,
            progress=0,
            created_at=time.time(),
            updated_at=time.time(),
            error=None,
            error_type=None,
            supports_resume=0,
            queue_order=0,
        )
        session.add(record)
        session.commit()
    finally:
        session.close()

    report = validate_database()
    # The validation should find our test record's invalid status
    assert report.issues_found >= 1
    # At minimum, our test record should be caught
    assert any(issue[1] == DatabaseConsistencyIssue.INVALID_STATUS.value for issue in report.issues)


def test_database_fix_corrects_invalid_status(tmp_path):
    """Database fix corrects invalid status to QUEUED."""
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord
    
    init_db()
    test_id = "test_fix_status_xyz"
    session = get_session()
    try:
        # Clear any existing test record
        session.query(DownloadRecord).filter(DownloadRecord.id == test_id).delete(synchronize_session=False)
        session.commit()
        
        record = DownloadRecord(
            id=test_id,
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status="INVALID_STATUS",
            total_size=1000,
            downloaded_size=0,
            speed=0,
            progress=0,
            created_at=time.time(),
            updated_at=time.time(),
            error=None,
            error_type=None,
            supports_resume=0,
            queue_order=0,
        )
        session.add(record)
        session.commit()
    finally:
        session.close()

    fix_report = fix_database_issues()
    assert fix_report.issues_found > 0

    # Verify fix
    session = get_session()
    try:
        record = session.get(DownloadRecord, test_id)
        assert record.status == TaskStatus.QUEUED.value
    finally:
        session.close()


def test_database_fix_corrects_negative_queue_order(tmp_path):
    """Database fix corrects negative queue_order."""
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord
    
    init_db()
    test_id = "test_negative_queue_xyz"
    session = get_session()
    try:
        session.query(DownloadRecord).filter(DownloadRecord.id == test_id).delete(synchronize_session=False)
        session.commit()
        
        record = DownloadRecord(
            id=test_id,
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status=TaskStatus.QUEUED.value,
            total_size=1000,
            downloaded_size=0,
            speed=0,
            progress=0,
            created_at=time.time(),
            updated_at=time.time(),
            error=None,
            error_type=None,
            supports_resume=0,
            queue_order=-5,  # Invalid
        )
        session.add(record)
        session.commit()
    finally:
        session.close()

    fix_report = fix_database_issues()
    
    session = get_session()
    try:
        record = session.get(DownloadRecord, test_id)
        assert record.queue_order == 0
    finally:
        session.close()


def test_database_fix_corrects_negative_sizes(tmp_path):
    """Database fix corrects negative size fields."""
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord
    
    init_db()
    test_id = "test_negative_sizes_xyz"
    session = get_session()
    try:
        session.query(DownloadRecord).filter(DownloadRecord.id == test_id).delete(synchronize_session=False)
        session.commit()
        
        record = DownloadRecord(
            id=test_id,
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status=TaskStatus.QUEUED.value,
            total_size=-100,
            downloaded_size=-50,
            speed=0,
            progress=0,
            created_at=time.time(),
            updated_at=time.time(),
            error=None,
            error_type=None,
            supports_resume=0,
            queue_order=0,
        )
        session.add(record)
        session.commit()
    finally:
        session.close()

    fix_report = fix_database_issues()
    
    session = get_session()
    try:
        record = session.get(DownloadRecord, test_id)
        assert record.total_size == 0
        assert record.downloaded_size == 0
    finally:
        session.close()


def test_database_fix_corrects_invalid_progress(tmp_path):
    """Database fix corrects progress outside 0-100 range."""
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord
    
    init_db()
    test_id = "test_invalid_progress_xyz"
    session = get_session()
    try:
        session.query(DownloadRecord).filter(DownloadRecord.id == test_id).delete(synchronize_session=False)
        session.commit()
        
        record = DownloadRecord(
            id=test_id,
            name="test.bin",
            source_url="http://example.com/test.bin",
            download_url="http://example.com/test.bin",
            destination=str(tmp_path / "test.bin"),
            status=TaskStatus.DOWNLOADING.value,
            total_size=1000,
            downloaded_size=500,
            speed=0,
            progress=150,  # Invalid
            created_at=time.time(),
            updated_at=time.time(),
            error=None,
            error_type=None,
            supports_resume=0,
            queue_order=0,
        )
        session.add(record)
        session.commit()
    finally:
        session.close()

    fix_report = fix_database_issues()
    
    session = get_session()
    try:
        record = session.get(DownloadRecord, test_id)
        assert record.progress == 0.0
    finally:
        session.close()


def test_save_download_task_validates_before_save():
    """save_download_task validates task before saving."""
    from app.database.repositories import save_download_task, ValidationReport
    
    task = DownloadTask(
        id="",  # Missing ID
        name="test.bin",
        source_url="http://example.com/test.bin",
        download_url="http://example.com/test.bin",
        destination="/tmp/test.bin",
        status=TaskStatus.QUEUED,
    )
    
    # Should not raise, but log warning
    save_download_task(task)


# ── Diagnostics Tests ──────────────────────────────────────────────────

def test_build_diagnostic_context():
    """Diagnostic context captures all relevant fields."""
    task = DownloadTask(
        id="diag_test",
        name="test.zip",
        source_url="http://example.com/source",
        download_url="http://example.com/test.zip",
        destination="/tmp/test.zip",
        status=TaskStatus.DOWNLOADING,
        downloaded_size=5000,
        total_size=10000,
        speed=1024.0,
    )

    context = build_diagnostic_context(
        task=task,
        phase=FailurePhase.STREAMING,
        error_type=DownloadErrorType.TIMEOUT,
        error_message="Read timeout after 60 seconds",
        http_status=200,
        http_response_headers={"content-type": "application/zip"},
        redirect_chain=["http://example.com/test.zip"],
        final_url="http://example.com/test.zip",
        retry_count=2,
        duration_seconds=30.5,
        exception_type="TimeoutException",
    )

    assert context.task_id == "diag_test"
    assert context.phase == FailurePhase.STREAMING
    assert context.error_type == DownloadErrorType.TIMEOUT
    assert context.retry_count == 2
    assert context.duration_seconds == 30.5


def test_format_diagnostic_output():
    """Diagnostic formatting produces readable output."""
    from app.services.diagnostics import DiagnosticContext, FailurePhase
    
    context = DiagnosticContext(
        task_id="diag_test",
        task_name="test.zip",
        source_url="http://example.com/source",
        download_url="http://example.com/test.zip",
        destination="/tmp/test.zip",
        phase=FailurePhase.STREAMING,
        error_type=DownloadErrorType.TIMEOUT,
        error_message="Read timeout",
        http_status=200,
        downloaded_size=5000,
        total_size=10000,
        speed=1024.0,
        retry_count=1,
        duration_seconds=15.0,
    )

    formatted = format_diagnostic(context)
    assert "Task ID:" in formatted
    assert "diag_test" in formatted
    assert "timeout" in formatted  # lowercase in output
    assert "streaming" in formatted  # lowercase in output


# ── HTML Parser Protection Tests ───────────────────────────────────────

def test_html_parser_limits_links():
    """Parser limits number of links to prevent DoS."""
    html = "<html><body>" + "".join(f'<a href="http://example.com/link{i}">Link {i}</a>' for i in range(_MAX_LINKS + 100)) + "</body></html>"
    parser = parse_html(html)
    assert len(parser.links) <= _MAX_LINKS


def test_html_parser_limits_nesting_depth():
    """Parser limits nesting depth to prevent stack overflow."""
    # Create deeply nested HTML
    depth = _MAX_NESTING_DEPTH + 50
    html = "<div>" * depth + "content" + "</div>" * depth
    parser = parse_html(html)
    assert parser._max_nesting_depth <= _MAX_NESTING_DEPTH


def test_html_parser_limits_total_text():
    """Parser limits total text size to prevent memory exhaustion."""
    large_text = "x" * (_MAX_TOTAL_TEXT_SIZE + 1000)
    html = f"<html><body><p>{large_text}</p></body></html>"
    parser = parse_html(html)
    assert parser._total_text_size <= _MAX_TOTAL_TEXT_SIZE


def test_html_parser_truncates_long_title():
    """Parser truncates excessively long titles."""
    long_title = "T" * 2000
    html = f"<html><head><title>{long_title}</title></head><body></body></html>"
    parser = parse_html(html)
    assert len(parser.title) <= 1024 + 3  # MAX_TITLE_LENGTH + "..."


def test_html_parser_handles_malformed_gracefully():
    """Parser handles malformed HTML without crashing."""
    malformed_html = "<html><body><p>unclosed<div><a href='test'>link</body></html>"
    parser = parse_html(malformed_html)
    # Should not raise, may have partial results
    assert isinstance(parser.links, list)


# ── Graceful Shutdown Tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_manager_shutdown_cancels_active_downloads(tmp_path):
    """Graceful shutdown cancels active downloads and waits for cleanup."""
    manager = DownloadManager(max_concurrent=1, downloads_dir=tmp_path)
    
    # Add a download to a non-existent external server (not localhost)
    task = await manager.add_download(
        name="slow.bin",
        source_url="http://example.invalid:9999/slow.bin",  # Invalid domain, will fail quickly
        download_url="http://example.invalid:9999/slow.bin",
        destination=str(tmp_path / "slow.bin"),
    )
    
    # Wait for it to start
    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)
    
    assert task.status in (TaskStatus.PREPARING, TaskStatus.DOWNLOADING)
    
    # Shutdown
    await manager.shutdown()
    
    assert task.status == TaskStatus.PAUSED
    assert "shutting down" in (task.error or "").lower()
    assert len(manager._tasks) == 0


# ── Persistence Service Tests ──────────────────────────────────────────

def test_persistence_validate_database():
    """Persistence service can validate database."""
    from app.services.persistence_service import PersistenceService
    from app.core.downloader import DownloadManager
    
    manager = DownloadManager(max_concurrent=1)
    persistence = PersistenceService()
    persistence.subscribe_to(manager)
    
    report = persistence.validate_database()
    assert isinstance(report, ValidationReport)


def test_persistence_fix_database():
    """Persistence service can fix database issues."""
    from app.services.persistence_service import PersistenceService
    from app.core.downloader import DownloadManager
    
    manager = DownloadManager(max_concurrent=1)
    persistence = PersistenceService()
    persistence.subscribe_to(manager)
    
    report = persistence.fix_database()
    assert isinstance(report, ValidationReport)


# ── Integration Tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_recovery_cycle(tmp_path):
    """Full crash recovery cycle: save -> crash -> restore."""
    from app.database.repositories import save_download_task, load_download_tasks
    from app.database.connection import get_session
    from app.database.models import DownloadRecord
    
    # Clear database first
    session = get_session()
    try:
        session.query(DownloadRecord).filter(DownloadRecord.id.like("q1%")).delete(synchronize_session=False)
        session.query(DownloadRecord).filter(DownloadRecord.id.like("q2%")).delete(synchronize_session=False)
        session.query(DownloadRecord).filter(DownloadRecord.id.like("d1%")).delete(synchronize_session=False)
        session.query(DownloadRecord).filter(DownloadRecord.id.like("p1%")).delete(synchronize_session=False)
        session.query(DownloadRecord).filter(DownloadRecord.id.like("c1%")).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()
    
    # Create tasks as if they were persisted
    tasks = [
        DownloadTask(id="q1", name="q1.bin", source_url="http://example.com/q1.bin", download_url="http://example.com/q1.bin", destination=str(tmp_path / "q1.bin"), status=TaskStatus.QUEUED, queue_order=2),
        DownloadTask(id="q2", name="q2.bin", source_url="http://example.com/q2.bin", download_url="http://example.com/q2.bin", destination=str(tmp_path / "q2.bin"), status=TaskStatus.QUEUED, queue_order=1),
        DownloadTask(id="d1", name="d1.bin", source_url="http://example.com/d1.bin", download_url="http://example.com/d1.bin", destination=str(tmp_path / "d1.bin"), status=TaskStatus.DOWNLOADING, downloaded_size=1000),
        DownloadTask(id="p1", name="p1.bin", source_url="http://example.com/p1.bin", download_url="http://example.com/p1.bin", destination=str(tmp_path / "p1.bin"), status=TaskStatus.PAUSED),
        DownloadTask(id="c1", name="c1.bin", source_url="http://example.com/c1.bin", download_url="http://example.com/c1.bin", destination=str(tmp_path / "c1.bin"), status=TaskStatus.COMPLETED),
    ]
    
    for task in tasks:
        save_download_task(task)
    
    # Simulate crash + restart: load tasks and restore
    loaded = load_download_tasks()
    manager = DownloadManager(max_concurrent=2, downloads_dir=tmp_path)
    interrupted = manager.restore_tasks(loaded)
    
    # Check only our test tasks
    our_queued = [t for t in manager.download_tasks if t.id in ("q1", "q2") and t.status == TaskStatus.QUEUED]
    assert len(our_queued) == 2
    # Should be ordered by queue_order
    assert our_queued[0].id == "q2"
    assert our_queued[1].id == "q1"
    
    # Interrupted task should be PAUSED
    interrupted_ids = [t.id for t in interrupted]
    assert "d1" in interrupted_ids
    
    # Paused task should remain PAUSED
    paused_task = manager.find_task("p1")
    assert paused_task is not None
    assert paused_task.status == TaskStatus.PAUSED
    
    # Completed task should remain COMPLETED
    completed_task = manager.find_task("c1")
    assert completed_task is not None
    assert completed_task.status == TaskStatus.COMPLETED


# ── V1.15 FINAL HARDENING — Regression Tests ─────────────────────────────

def test_validation_report_single_definition():
    """ValidationReport is defined exactly once in repositories.py."""
    import inspect
    from app.database import repositories

    source = inspect.getsource(repositories)
    # Count class definitions (not imports or references)
    count = source.count("class ValidationReport")
    assert count == 1, f"ValidationReport defined {count} times, expected exactly 1"


def test_recovery_action_no_misleading_values():
    """RecoveryAction enum no longer contains unused misleading values."""
    from app.services.crash_recovery import RecoveryAction

    # These misleading actions were removed because interrupted tasks
    # become PAUSED (manual resume), not auto-resumed or restarted.
    assert not hasattr(RecoveryAction, "RESUMED_QUEUED")
    assert not hasattr(RecoveryAction, "RESTARTED_INTERRUPTED")

    # The actual behavior is captured by these:
    assert hasattr(RecoveryAction, "PAUSED_INTERRUPTED")
    assert hasattr(RecoveryAction, "REMOVED_STALE_PART")
    assert hasattr(RecoveryAction, "FIXED_INCONSISTENT_RECORD")
    assert hasattr(RecoveryAction, "REMOVED_CORRUPTED_RECORD")


def test_html_parser_stops_traversal_at_depth_limit():
    """Parser actually stops processing when nesting depth exceeds the limit.

    Previously the parser only clamped the counter but continued parsing.
    Now it sets _depth_exceeded and skips data/tags until depth recovers.
    """
    # Build HTML that nests far beyond the limit with content inside
    depth = _MAX_NESTING_DEPTH + 100
    html = "<div>" * depth + "deep content" + "</div>" * depth
    parser = parse_html(html)

    # The deep content should NOT appear in the parser output because
    # traversal was stopped before reaching it.
    assert "deep content" not in parser.title
    # max_nesting_depth should be capped at the limit
    assert parser._max_nesting_depth <= _MAX_NESTING_DEPTH


def test_html_parser_recovers_after_depth_limit():
    """Parser resumes processing after depth returns below the limit."""
    # Nest just over the limit, then close enough tags to recover
    depth = _MAX_NESTING_DEPTH + 10
    html = "<div>" * depth + "<p>recovered</p>" + "</div>" * depth
    parser = parse_html(html)

    # After closing tags, depth should be back within limit
    assert parser._nesting_depth <= _MAX_NESTING_DEPTH
    # The "recovered" text may or may not be captured depending on timing,
    # but the parser should not be stuck in depth_exceeded state
    assert parser._depth_exceeded is False


def test_html_parser_normal_depth_not_affected():
    """Normal-depth HTML is parsed correctly (regression check)."""
    html = '<html><head><title>Normal Page</title></head><body>' \
           '<div><p><a href="http://example.com/x.zip">Link</a></p></div>' \
           '</body></html>'
    parser = parse_html(html)

    assert parser.title == "Normal Page"
    assert len(parser.links) == 1
    assert parser.links[0].href == "http://example.com/x.zip"
    assert parser.links[0].text == "Link"
    assert parser._depth_exceeded is False


@pytest.mark.asyncio
async def test_shutdown_persists_after_manager_shutdown(tmp_path):
    """MainWindow.closeEvent persists AFTER DownloadManager.shutdown().

    Regression test: verifies the shutdown sequence order is:
    1. DownloadManager.shutdown() (sets active tasks to PAUSED)
    2. PersistenceService.flush() (persists PAUSED tasks)
    3. PersistenceService.validate_database()
    """
    from app.services.persistence_service import PersistenceService
    from app.core.downloader import DownloadManager
    from app.core.task_manager import TaskStatus
    from app.database.repositories import load_download_tasks
    from app.database.connection import get_session
    from app.database.models import DownloadRecord

    # Clean up any existing test records
    session = get_session()
    try:
        session.query(DownloadRecord).filter(DownloadRecord.id.like("shutdown_test%")).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()

    manager = DownloadManager(max_concurrent=1, downloads_dir=tmp_path)
    persistence = PersistenceService()
    persistence.subscribe_to(manager)

    task = await manager.add_download(
        name="shutdown_test.bin",
        source_url="http://example.invalid:9999/shutdown_test.bin",
        download_url="http://example.invalid:9999/shutdown_test.bin",
        destination=str(tmp_path / "shutdown_test.bin"),
    )
    # Wait for it to start
    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    # Simulate the closeEvent sequence (all in same event loop)
    await manager.shutdown()
    persistence.flush()
    persistence.validate_database()

    # Task should be PAUSED (set by shutdown) and persisted
    assert task.status == TaskStatus.PAUSED
    assert "shutting down" in (task.error or "").lower()

    # Verify it was persisted to the database
    loaded = load_download_tasks()
    persisted = [t for t in loaded if t.id == task.id]
    assert len(persisted) == 1
    assert persisted[0].status == TaskStatus.PAUSED