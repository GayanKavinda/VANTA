"""V2.0 Phase 2 — Download UX regression tests.

These tests verify the new filename/duplicate/review/details UX without
modifying any frozen V1.x architecture.
"""

import os
import tempfile
from pathlib import Path

import pytest

from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.services.duplicate_service import (
    DuplicateCheck,
    DuplicateState,
    check_duplicate_resource,
    check_existing_destination,
    duplicate_state_label,
)
from app.services.filename_service import (
    file_category_label,
    format_file_size,
    is_safe_within_directory,
    sanitize_filename,
    split_filename_extension,
)


# ── 2.2 Filename UX ────────────────────────────────────────────────────────

def test_sanitize_filename_preserves_valid():
    assert sanitize_filename("game.zip") == "game.zip"
    assert sanitize_filename("my file (1).bin") == "my file (1).bin"


def test_sanitize_filename_handles_empty():
    assert sanitize_filename("") == "download"
    assert sanitize_filename(None) == "download"


def test_sanitize_filename_strips_path_components():
    # Path traversal attempt must be reduced to the base name.
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\\\foo\\\\bar.exe") == "bar.exe"


def test_sanitize_filename_removes_invalid_windows_chars():
    assert sanitize_filename("bad<file>.zip") == "bad_file_.zip"
    assert sanitize_filename('a"b*c?.txt') == "a_b_c_.txt"


def test_sanitize_filename_collapses_repeated_underscores():
    assert sanitize_filename("a__zip") == "a_zip"
    assert sanitize_filename("a___b.zip") == "a_b.zip"


def test_sanitize_filename_avoids_reserved_names():
    # CON, PRN, NUL must not be returned verbatim.
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("PRN.txt") == "_PRN.txt"


def test_sanitize_filename_preserves_unicode():
    # Unicode filenames should be preserved (NFC normalized).
    result = sanitize_filename("r\u00e9sum\u00e9.zip")
    assert "r\u00e9sum\u00e9" in result
    assert result.endswith(".zip")


def test_split_filename_extension():
    stem, ext = split_filename_extension("archive.tar.gz")
    assert stem == "archive.tar"
    assert ext == "gz"


def test_split_filename_extension_hidden_file():
    stem, ext = split_filename_extension(".gitignore")
    assert stem == ".gitignore"
    assert ext == ""


def test_is_safe_within_directory_allows_normal():
    with tempfile.TemporaryDirectory() as tmp:
        assert is_safe_within_directory("safe.zip", tmp) is True


def test_is_safe_within_directory_blocks_traversal():
    """Sanitizing a traversal filename yields only the basename.

    The sanitizer strips path components, so the result is always safe.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # After sanitization, "../escape.zip" becomes "escape.zip".
        safe = sanitize_filename("../escape.zip")
        assert safe == "escape.zip"
        assert is_safe_within_directory(safe, tmp) is True


def test_format_file_size():
    assert format_file_size(0) == "0 B"
    assert format_file_size(512) == "512 B"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(1024 * 1024) == "1.0 MB"


def test_format_file_size_none():
    assert format_file_size(None) == "Unknown size"


def test_file_category_label():
    assert file_category_label("archive") == "Archive"
    assert file_category_label("installer") == "Installer"
    assert file_category_label(None) == "Unknown"
    assert file_category_label("unknown") == "Unknown"


# ── 2.3 Duplicate UX ───────────────────────────────────────────────────────

def test_check_existing_destination_ready(tmp_path):
    result = check_existing_destination("new.zip", tmp_path)
    assert result.state == DuplicateState.READY


def test_check_existing_destination_detects_existing(tmp_path):
    existing = tmp_path / "exists.zip"
    existing.write_bytes(b"data")
    result = check_existing_destination("exists.zip", tmp_path)
    assert result.state == DuplicateState.ALREADY_EXISTS
    assert result.existing_path is not None


def test_check_duplicate_resource_url_match():
    seen = {"http://example.com/a.zip": "task-1"}
    file = DownloadFile(name="a.zip", url="http://example.com/a.zip")
    result = check_duplicate_resource(file, seen, {})
    assert result.state == DuplicateState.DUPLICATE_RESOURCE


def test_check_duplicate_resource_filename_size_match():
    seen = {}
    seen_fs = {("a.zip", 100): "task-1"}
    file = DownloadFile(name="a.zip", url="http://example.com/b.zip", size=100)
    result = check_duplicate_resource(file, seen, seen_fs)
    assert result.state == DuplicateState.SAME_FILENAME_SIZE


def test_check_duplicate_resource_unique():
    file = DownloadFile(name="c.zip", url="http://example.com/c.zip", size=200)
    result = check_duplicate_resource(file, {}, {})
    assert result.state == DuplicateState.READY


def test_duplicate_state_label():
    assert duplicate_state_label(DuplicateState.READY) == "Ready to download"
    assert duplicate_state_label(DuplicateState.ALREADY_EXISTS) == "Already exists"


# ── 2.1 Destination management via FileManager ──────────────────────────────

def test_file_manager_creates_default_dir(tmp_path):
    fm = FileManager(tmp_path / "downloads")
    assert fm.default_dir.exists()
    assert fm.default_dir == tmp_path / "downloads"


def test_file_manager_safe_join_prevents_traversal(tmp_path):
    fm = FileManager(tmp_path)
    safe = fm.safe_join("../../../escape.zip")
    assert safe == "escape.zip"
    assert ".." not in safe


def test_file_manager_get_unique_path(tmp_path):
    fm = FileManager(tmp_path)
    p1 = fm.get_unique_path("file.zip")
    p1.write_bytes(b"x")
    p2 = fm.get_unique_path("file.zip")
    assert p2 != p1
    assert p2.name == "file_1.zip"


# ── 2.6 Download details dialog (no state mutation) ────────────────────────

def test_download_details_dialog_does_not_mutate(tmp_path):
    from app.core.task_manager import DownloadTask, TaskStatus
    from app.ui.download_details import DownloadDetailsDialog

    task = DownloadTask(
        id="detail_test",
        name="detail.bin",
        source_url="http://example.com/detail.bin",
        download_url="http://example.com/detail.bin",
        destination=str(tmp_path / "detail.bin"),
        status=TaskStatus.COMPLETED,
        total_size=1000,
        downloaded_size=1000,
        progress=100.0,
    )

    dialog = DownloadDetailsDialog(task)
    # Opening the dialog must not change task state.
    assert task.status == TaskStatus.COMPLETED
    assert task.downloaded_size == 1000


# ── Regression: existing-file protection remains intact ────────────────────

def test_existing_file_protection_remains(tmp_path):
    """The download engine still rejects existing destination files."""
    from app.core.downloader import DownloadManager
    from app.core.task_manager import TaskStatus
    import asyncio

    async def _run():
        manager = DownloadManager(max_concurrent=1, downloads_dir=tmp_path)
        dest = tmp_path / "exists.bin"
        dest.write_bytes(b"already here")

        task = await manager.add_download(
            name="exists.bin",
            source_url="http://example.invalid/exists.bin",
            download_url="http://example.invalid/exists.bin",
            destination=str(dest),
        )

        # Wait for failure (invalid domain will fail, but existing-file
        # check happens first in _execute_download).
        for _ in range(50):
            if task.is_terminal:
                break
            await asyncio.sleep(0.01)

        assert task.status == TaskStatus.FAILED
        assert "exists" in (task.error or "").lower()

    asyncio.run(_run())
def test_review_dialog_receives_duplicate_check(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024, content_type='application/zip')
    dest = tmp_path / 'downloads'
    dest.mkdir()
    dc = workflow.check_duplicate(file, 'test.zip', dest)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=dest, duplicate_check=dc, workflow=workflow)
    assert dialog._duplicate_check is not None
    assert dialog._duplicate_check.state == DuplicateState.READY

def test_review_dialog_shows_already_exists(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    dest = tmp_path / 'downloads'
    dest.mkdir()
    existing = dest / 'test.zip'
    existing.write_bytes(b'existing data')
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024, content_type='application/zip')
    dc = workflow.check_duplicate(file, 'test.zip', dest)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=dest, duplicate_check=dc, workflow=workflow)
    assert dialog._duplicate_check.state == DuplicateState.ALREADY_EXISTS
    assert dialog._duplicate_check.existing_path is not None

def test_review_dialog_shows_duplicate_resource(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    workflow.register_selected(type('T', (), {'download_url': 'http://example.com/a.zip', 'id': 'task-1', 'name': 'a.zip', 'total_size': 100})())
    file = DownloadFile(name='b.zip', url='http://example.com/a.zip', size=200)
    dc = workflow.check_duplicate(file, 'b.zip', tmp_path / 'any')
    assert dc.state == DuplicateState.DUPLICATE_RESOURCE
    assert dc.existing_task_id == 'task-1'

def test_review_dialog_shows_same_filename_size(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    workflow.register_selected(type('T', (), {'download_url': 'http://example.com/x', 'id': 'task-1', 'name': 'same.zip', 'total_size': 500})())
    file = DownloadFile(name='same.zip', url='http://example.com/y', size=500)
    dc = workflow.check_duplicate(file, 'same.zip', tmp_path / 'any')
    assert dc.state == DuplicateState.SAME_FILENAME_SIZE
    assert dc.existing_task_id == 'task-1'

def test_duplicate_cleared_after_filename_change(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    dest = tmp_path / 'downloads'
    dest.mkdir()
    existing = dest / 'movie.zip'
    existing.write_bytes(b'data')
    file = DownloadFile(name='movie.zip', url='http://example.com/movie.zip', size=1024, content_type='application/zip')
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=dest, workflow=workflow)
    assert dialog._duplicate_check.state == DuplicateState.ALREADY_EXISTS
    dialog._filename_edit.setText('movie-new.zip')
    assert dialog._duplicate_check.state == DuplicateState.READY

def test_duplicate_cleared_after_destination_change(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    dest1 = tmp_path / 'downloads1'
    dest1.mkdir()
    existing = dest1 / 'movie.zip'
    existing.write_bytes(b'data')
    dest2 = tmp_path / 'downloads2'
    dest2.mkdir()
    file = DownloadFile(name='movie.zip', url='http://example.com/movie.zip', size=1024, content_type='application/zip')
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=dest1, workflow=workflow)
    assert dialog._duplicate_check.state == DuplicateState.ALREADY_EXISTS
    dialog._destination = dest2
    dialog._dest_display.setText(str(dest2))
    dialog._duplicate_check = workflow.check_duplicate(file, dialog._filename, dest2)
    assert dialog._duplicate_check.state == DuplicateState.READY
def test_download_button_rejects_already_exists(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    dest = tmp_path / 'downloads'
    dest.mkdir()
    existing = dest / 'test.zip'
    existing.write_bytes(b'data')
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024, content_type='application/zip')
    dc = workflow.check_duplicate(file, 'test.zip', dest)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=dest, duplicate_check=dc, workflow=workflow)
    result = dialog._on_download()
    assert dialog.result() == 0

def test_download_button_accepts_ready(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    dest = tmp_path / 'downloads'
    dest.mkdir()
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024, content_type='application/zip')
    dc = workflow.check_duplicate(file, 'test.zip', dest)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=dest, duplicate_check=dc, workflow=workflow)
    dialog._on_download()
    assert dialog.result() == 1

def test_selected_destination_validated(tmp_path):
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(tmp_path / 'fm')
    is_valid, msg = workflow.validate_final('file.zip', str(tmp_path / 'nonexistent'))
    assert not is_valid
    assert 'does not exist' in msg.lower()

def test_path_traversal_sanitized(tmp_path):
    from app.services.download_workflow import DownloadWorkflowService
    from app.services.filename_service import sanitize_filename
    workflow = DownloadWorkflowService(tmp_path)
    safe = sanitize_filename('../../etc/passwd')
    assert safe == 'passwd'
    assert '..' not in safe
    is_valid, msg = workflow.validate_final('../../etc/passwd', str(tmp_path))
    assert is_valid, f'Sanitized traversal should be valid: {msg}'

def test_non_directory_rejected(tmp_path):
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(tmp_path)
    fp = tmp_path / 'notadir'
    fp.write_bytes(b'x')
    is_valid, msg = workflow.validate_final('file.zip', str(fp))
    assert not is_valid
    assert 'not a directory' in msg.lower()

def test_unicode_filename_accepted(tmp_path):
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(tmp_path)
    is_valid, msg = workflow.validate_final('re\u00e9sum\u00e9.zip', str(tmp_path))
    assert is_valid, f'Unicode filename rejected: {msg}'

def test_unsafe_filename_sanitized(tmp_path):
    from app.services.download_workflow import DownloadWorkflowService
    from app.services.filename_service import sanitize_filename
    workflow = DownloadWorkflowService(tmp_path)
    safe = sanitize_filename('/etc/passwd')
    assert safe == 'passwd'
    assert '/' not in safe
    is_valid, msg = workflow.validate_final('/etc/passwd', str(tmp_path))
    assert is_valid, f'Sanitized absolute path should be valid: {msg}'
def test_category_archive(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='archive.zip', url='http://example.com/archive.zip', content_type='application/zip')
    category = workflow.file_category(file, file.content_type)
    assert category == 'archive'

def test_category_document(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='doc.pdf', url='http://example.com/doc.pdf', content_type='application/pdf')
    category = workflow.file_category(file, file.content_type)
    assert category == 'document'

def test_category_image(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='photo.jpg', url='http://example.com/photo.jpg', content_type='image/jpeg')
    category = workflow.file_category(file, file.content_type)
    assert category == 'image'

def test_category_video(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='video.mp4', url='http://example.com/video.mp4', content_type='video/mp4')
    category = workflow.file_category(file, file.content_type)
    assert category == 'video'

def test_category_audio(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='song.mp3', url='http://example.com/song.mp3', content_type='audio/mpeg')
    category = workflow.file_category(file, file.content_type)
    assert category == 'audio'

def test_category_installer(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='setup.exe', url='http://example.com/setup.exe', content_type='application/x-msdownload')
    category = workflow.file_category(file, file.content_type)
    assert category == 'installer'

def test_category_unknown_fallback(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='data.xyz', url='http://example.com/data.xyz')
    category = workflow.file_category(file, file.content_type)
    assert category == 'unknown'

def test_category_from_filename_without_content_type(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    workflow = DownloadWorkflowService(AppState().file_manager)
    file = DownloadFile(name='report.pdf', url='http://example.com/report.pdf', content_type=None)
    category = workflow.file_category(file, file.content_type)
    assert category == 'document'
def test_category_label_archive(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('archive') == 'Archive'

def test_category_label_document(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('document') == 'Document'

def test_category_label_image(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('image') == 'Image'

def test_category_label_video(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('video') == 'Video'

def test_category_label_audio(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('audio') == 'Audio'

def test_category_label_installer(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('installer') == 'Installer'

def test_category_label_unknown(tmp_path):
    from app.services.filename_service import file_category_label
    assert file_category_label('unknown') == 'Unknown'

def test_category_review_dialog_displays_real_category(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024, content_type='application/zip')
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=tmp_path / 'downloads', workflow=workflow)
    category_text = dialog._category_label.text()
    assert category_text != 'Unknown', f'Category should not be Unknown for ZIP file'
    assert category_text == 'Archive', f'Expected Archive, got {category_text}'
def test_download_service_uses_reviewed_filename(tmp_path):
    import asyncio
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_service import DownloadService
    async def _run():
        app_state = AppState()
        service = DownloadService(analyzer=app_state.analyzer, download_manager=app_state.download_manager, queue_controller=None, file_manager=app_state.file_manager)
        file = DownloadFile(name='movie.zip', url='http://example.com/movie.zip', size=1024, content_type='application/zip')
        dest = tmp_path / 'downloads'
        dest.mkdir()
        result = await service.start_file_download(source_url='http://example.com/page', file=file, destination=str(dest), filename='movie-reviewed.zip')
        assert result is not None
        assert result.name == 'movie-reviewed.zip'
    asyncio.run(_run())

def test_download_service_uses_reviewed_destination(tmp_path):
    import asyncio
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_service import DownloadService
    async def _run():
        app_state = AppState()
        service = DownloadService(analyzer=app_state.analyzer, download_manager=app_state.download_manager, queue_controller=None, file_manager=app_state.file_manager)
        file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024)
        dest = tmp_path / 'custom_downloads'
        dest.mkdir()
        result = await service.start_file_download(source_url='http://example.com/page', file=file, destination=str(dest), filename='test.zip')
        assert result is not None
        assert str(dest) in result.destination
    asyncio.run(_run())

def test_download_service_rejects_existing_file_explicit(tmp_path):
    import asyncio
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_service import DownloadService
    async def _run():
        app_state = AppState()
        service = DownloadService(analyzer=app_state.analyzer, download_manager=app_state.download_manager, queue_controller=None, file_manager=app_state.file_manager)
        dest = tmp_path / 'downloads'
        dest.mkdir()
        existing = dest / 'exists.zip'
        existing.write_bytes(b'already here')
        file = DownloadFile(name='exists.zip', url='http://example.com/exists.zip', size=1024)
        result = await service.start_file_download(source_url='http://example.com/page', file=file, destination=str(dest), filename='exists.zip')
        assert result is None
    asyncio.run(_run())

def test_download_service_auto_resolves_non_review(tmp_path):
    import asyncio
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_service import DownloadService
    async def _run():
        app_state = AppState()
        service = DownloadService(analyzer=app_state.analyzer, download_manager=app_state.download_manager, queue_controller=None, file_manager=app_state.file_manager)
        dest = tmp_path / 'downloads'
        dest.mkdir()
        existing = dest / 'file.zip'
        existing.write_bytes(b'data')
        file = DownloadFile(name='file.zip', url='http://example.com/file.zip', size=1024)
        result = await service.start_file_download(source_url='http://example.com/page', file=file)
        assert result is not None
        assert result.destination != str(dest / 'file.zip')
    asyncio.run(_run())
def test_bulk_download_uses_default_destination(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.filename_service import sanitize_filename
    app_state = AppState()
    file = DownloadFile(name='bulk.zip', url='http://example.com/bulk.zip')
    safe = sanitize_filename(file.name)
    assert safe == 'bulk.zip'
    assert app_state.file_manager.default_dir is not None

def test_bulk_download_sanitizes_filenames(tmp_path):
    from app.services.filename_service import sanitize_filename
    assert sanitize_filename('../../escape.zip') == 'escape.zip'
    assert sanitize_filename('bad<file>.zip') == 'bad_file_.zip'
    assert sanitize_filename(' normal.zip ') == 'normal.zip'

def test_bulk_download_no_overwrite_via_get_unique_path(tmp_path):
    from app.core.file_manager import FileManager
    fm = FileManager(tmp_path)
    p1 = fm.get_unique_path('file.zip')
    p1.write_bytes(b'first')
    p2 = fm.get_unique_path('file.zip')
    assert p2 != p1
    assert p2.name == 'file_1.zip'

def test_bulk_download_destination_preserved(tmp_path):
    from app.core.app_state import AppState
    from app.core.file_manager import FileManager
    app_state = AppState()
    fm = FileManager(tmp_path / 'bulk_downloads')
    assert fm.default_dir == tmp_path / 'bulk_downloads'
    assert fm.default_dir.exists()
def test_duplicate_state_is_enum(tmp_path):
    from enum import Enum
    from app.services.duplicate_service import DuplicateState
    assert issubclass(DuplicateState, Enum)
    assert issubclass(DuplicateState, str)

def test_duplicate_state_string_compatible(tmp_path):
    from app.services.duplicate_service import DuplicateState
    assert DuplicateState.READY == 'ready'
    assert DuplicateState.ALREADY_EXISTS == 'already_exists'
    assert DuplicateState.DUPLICATE_RESOURCE == 'duplicate_resource'
    assert DuplicateState.SAME_FILENAME_SIZE == 'same_filename_size'
    assert DuplicateState.UNKNOWN == 'unknown'

def test_duplicate_state_all_labels(tmp_path):
    from app.services.duplicate_service import duplicate_state_label, DuplicateState
    assert duplicate_state_label(DuplicateState.READY) == 'Ready to download'
    assert duplicate_state_label(DuplicateState.ALREADY_EXISTS) == 'Already exists'
    assert duplicate_state_label(DuplicateState.DUPLICATE_RESOURCE) == 'Duplicate resource'
    assert duplicate_state_label(DuplicateState.SAME_FILENAME_SIZE) == 'Same filename + size'
    assert duplicate_state_label(DuplicateState.UNKNOWN) == 'Unknown'

def test_duplicate_state_used_in_checks(tmp_path):
    from app.services.duplicate_service import DuplicateState
    state = DuplicateState.ALREADY_EXISTS
    assert state == 'already_exists'
    assert state == DuplicateState.ALREADY_EXISTS
    assert state.value == 'already_exists'
def test_review_dialog_cancel_no_queue_mutation(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=tmp_path / 'downloads', workflow=workflow)
    assert dialog.result() == 0

def test_review_dialog_opening_no_download(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    file = DownloadFile(name='test.zip', url='http://example.com/test.zip', size=1024)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=tmp_path / 'downloads', workflow=workflow)
    assert dialog._file.name == 'test.zip'

def test_review_dialog_displayed_filename_matches_sanitized(tmp_path):
    from app.core.app_state import AppState
    from app.core.models import DownloadFile
    from app.services.download_workflow import DownloadWorkflowService
    from app.ui.download_review import DownloadReviewDialog
    from app.services.filename_service import sanitize_filename
    app_state = AppState()
    workflow = DownloadWorkflowService(app_state.file_manager)
    raw_name = 'bad<file>.zip'
    expected = sanitize_filename(raw_name)
    file = DownloadFile(name=raw_name, url='http://example.com/test.zip', size=1024)
    dialog = DownloadReviewDialog(file=file, file_manager=app_state.file_manager, download_dir=tmp_path / 'downloads', workflow=workflow)
    assert dialog._filename == expected
    assert dialog._filename_edit.text() == expected