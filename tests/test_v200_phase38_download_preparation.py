"""V2.0 Phase 3.8 — Analysis Workflow Completion & Download Preparation UX.

Verifies the new bulk review step that bridges "Download Selected" in the
analysis workspace to the existing Phase 2 review/queue pipeline.

Key invariants under test:
* the review dialog is the gate — only READY resources are accepted;
* filename / destination / duplicate / category / size are surfaced using the
  existing DownloadWorkflowService (no reimplemented validation);
* invalid / already-existing / conflicting resources cannot be confirmed;
* accepted entries carry a reviewed filename + destination suitable for the
  existing `start_file_download` reviewed flow;
* cancel/back leaves `accepted` empty (no accidental download);
* the category filter narrows the visible working set.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from app.core.file_manager import FileManager
from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import AnalysisViewModel, ResourceView
from app.services.download_workflow import DownloadWorkflowService
from app.services.filename_service import format_file_size, sanitize_filename


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _view(
    name: str,
    *,
    size: int | None = 2048,
    content_type: str | None = "application/zip",
    url: str | None = None,
    confidence: str = ConfidenceLevel.HIGH,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=url or f"https://example.com/{name}",
            size=size,
            content_type=content_type,
        ),
        confidence=confidence,
        score=80,
    )


def _build_dialog(resource_views, dest_dir):
    fm = FileManager(dest_dir)
    workflow = DownloadWorkflowService(fm)
    from app.ui.bulk_review import BulkReviewDialog

    return BulkReviewDialog(
        source_url="https://example.com/page",
        resource_views=resource_views,
        file_manager=fm,
        download_dir=dest_dir,
        workflow=workflow,
    )


@pytest.fixture
def dest_dir(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir()
    return d


def _displayed_names(home):
    return [row._view.file.name for row in home._result_area._resource_rows]


def _populate(home, views):
    vm = AnalysisViewModel(
        title="T", source="https://example.com/page", status="ready",
        resources=views, has_resources=True,
    )
    home.set_current_url_for_result("https://example.com/page")
    home.show_analysis_view(vm)
    QTest.qWait(20)


# ---------------------------------------------------------------------------
# Dialog construction & display
# ---------------------------------------------------------------------------

def test_dialog_lists_all_selected_resources(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip"), _view("b.zip")], dest_dir)
    assert len(dialog._entries) == 2


def test_dialog_shows_destination(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    assert str(dest_dir) in dialog._dest_display.text()


def test_dialog_shows_proposed_filename_per_resource(qapp, dest_dir):
    dialog = _build_dialog([_view("setup.exe")], dest_dir)
    assert dialog._entries[0].filename_edit.text() == "setup.exe"


def test_dialog_shows_category_and_size(qapp, dest_dir):
    dialog = _build_dialog([_view("game.zip", size=2048)], dest_dir)
    entry = dialog._entries[0]
    assert entry.category_label == "Archive"
    assert entry.size_label == format_file_size(2048)


def test_dialog_summary_counts_resources(qapp, dest_dir):
    views = [_view("a.zip"), _view("b.zip"), _view("c.txt", size=None, content_type="text/plain")]
    dialog = _build_dialog(views, dest_dir)
    assert dialog._summary.text().startswith("3 resources selected")
    assert "Ready: 3" in dialog._summary.text()


# ---------------------------------------------------------------------------
# Readiness & duplicate states (reusing DownloadWorkflowService)
# ---------------------------------------------------------------------------

def test_ready_resource_is_ready(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    ready, text, kind = dialog._evaluate(dialog._entries[0])
    assert ready is True
    assert kind == "ready"
    assert "Ready" in text


def test_already_exists_file_is_ready_with_auto_rename(qapp, dest_dir):
    (dest_dir / "a.zip").write_bytes(b"existing data")
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    ready, text, kind = dialog._evaluate(dialog._entries[0])
    assert ready is True
    assert kind == "ready"
    assert "Auto rename" in text


def test_download_button_enabled_when_auto_rename(qapp, dest_dir):
    (dest_dir / "a.zip").write_bytes(b"existing data")
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    dialog._refresh_summary()
    assert dialog._download_btn.isEnabled() is True


def test_download_button_enabled_when_at_least_one_ready(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip"), _view("b.zip")], dest_dir)
    dialog._refresh_summary()
    assert dialog._download_btn.isEnabled() is True


# ---------------------------------------------------------------------------
# Invalid / blocked resources cannot be confirmed
# ---------------------------------------------------------------------------

def test_empty_filename_sanitizes_to_valid(qapp, dest_dir):
    """An empty edited filename sanitizes to a safe default (never raw-empty),
    so no invalid/empty path can reach the queue."""
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    entry = dialog._entries[0]
    entry.filename_edit.setText("")
    reviewed = sanitize_filename(entry.filename_edit.text())
    assert reviewed == "download"  # sanitize_filename default
    ready, _text, kind = dialog._evaluate(entry)
    assert kind != "conflict"


def test_invalid_destination_is_blocked(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    dialog._download_dir = Path(dest_dir) / "does-not-exist"
    ready, text, kind = dialog._evaluate(dialog._entries[0])
    assert ready is False
    assert kind == "invalid"
    assert "Invalid" in text


def test_traversal_filename_sanitized(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    entry = dialog._entries[0]
    entry.filename_edit.setText("../../etc/passwd")
    reviewed = sanitize_filename(entry.filename_edit.text())
    assert ".." not in reviewed
    assert "/" not in reviewed


# ---------------------------------------------------------------------------
# Accepted entries carry reviewed filename + destination
# ---------------------------------------------------------------------------

def test_accepted_entries_carry_reviewed_filename_and_dest(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    entry = dialog._entries[0]
    entry.filename_edit.setText("renamed.zip")
    dialog._on_download()

    assert len(dialog.accepted_entries) == 1
    _entry, filename, dest = dialog.accepted_entries[0]
    assert filename == "renamed.zip"
    assert dest == dest_dir


def test_accepted_excludes_not_ready_resources(qapp, dest_dir):
    (dest_dir / "exists.zip").write_bytes(b"already here")
    views = [_view("ready.zip"), _view("exists.zip")]
    dialog = _build_dialog(views, dest_dir)
    dialog._on_download()

    accepted_names = [fn for (_e, fn, _d) in dialog.accepted_entries]
    assert "ready.zip" in accepted_names
    assert "exists.zip" not in accepted_names


def test_intra_bulk_filename_conflict_flagged(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip"), _view("b.zip")], dest_dir)
    e1, e2 = dialog._entries
    e1.filename_edit.setText("same.zip")
    e2.filename_edit.setText("same.zip")
    dialog._recalc_all()
    assert dialog._evaluate(e1)[0] is False
    assert dialog._evaluate(e2)[0] is False
    assert dialog._evaluate(e1)[2] == "conflict"


def test_intra_bulk_conflict_resolved_by_renaming_one(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip"), _view("b.zip")], dest_dir)
    e1, e2 = dialog._entries
    e1.filename_edit.setText("same.zip")
    e2.filename_edit.setText("other.zip")
    dialog._recalc_all()
    assert dialog._evaluate(e1)[0] is True
    assert dialog._evaluate(e2)[0] is True


# ---------------------------------------------------------------------------
# Cancel / back does not populate accepted
# ---------------------------------------------------------------------------

def test_cancel_leaves_accepted_empty(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    dialog.reject()
    assert dialog.accepted_entries == []


def test_back_button_rejects(qapp, dest_dir):
    dialog = _build_dialog([_view("a.zip")], dest_dir)
    back_btn = next(
        btn for btn in dialog.findChildren(QPushButton) if btn.text() == "Back"
    )
    back_btn.click()
    QTest.qWait(5)
    assert dialog.accepted_entries == []
    assert dialog.result() != 1


# ---------------------------------------------------------------------------
# Routing contract: accepted entries feed the reviewed start_file_download flow
# ---------------------------------------------------------------------------

def test_accepted_entries_route_through_reviewed_flow(tmp_path, qapp, monkeypatch):
    """The consumer of BulkReviewDialog.accepted invokes start_file_download
    with the reviewed filename + destination (never a direct downloader)."""
    from app.core.app_state import AppState
    from app.services.download_service import DownloadService

    app_state = AppState()
    dest = tmp_path / "downloads"
    dest.mkdir()

    service = DownloadService(
        analyzer=app_state.analyzer,
        download_manager=app_state.download_manager,
        file_manager=app_state.file_manager,
    )

    recorded = []

    async def fake_start(source_url, file, destination=None, filename=None, **kw):
        recorded.append((file.name, destination, filename))
        return type("T", (), {"id": "fake", "name": filename})()

    monkeypatch.setattr(service, "start_file_download", fake_start)

    views = [
        _view("game.zip"),
        _view("installer.exe", size=None, content_type="application/x-msdownload"),
    ]
    dialog = _build_dialog(views, dest)
    dialog._entries[0].filename_edit.setText("renamed-game.zip")
    dialog._on_download()

    async def _run():
        for entry, filename, d in dialog.accepted_entries:
            await service.start_file_download(
                source_url="https://example.com/page",
                file=entry.view.file,
                destination=str(d),
                filename=filename,
            )

    asyncio.run(_run())

    assert len(recorded) == 2
    forwarded_filenames = [fn for (_name, _dest, fn) in recorded]
    assert "renamed-game.zip" in forwarded_filenames
    assert all(fn is not None for fn in forwarded_filenames)


# ---------------------------------------------------------------------------
# Category filter UI (Phase 3.8 addition to the filtering UX)
# ---------------------------------------------------------------------------

def test_category_filter_ui_reduces_list(qapp):
    from app.ui.pages.home_page import HomePage

    home = HomePage()
    home.show()
    QTest.qWait(10)

    views = [
        _view("setup.exe", content_type="application/x-msdownload"),
        _view("game.zip", content_type="application/zip"),
        _view("readme.txt", size=None, content_type="text/plain"),
    ]
    _populate(home, views)

    assert len(_displayed_names(home)) == 3

    home._result_area._filter_combo.setCurrentIndex(5)  # Category
    QTest.qWait(10)
    home._result_area._category_combo.setCurrentIndex(0)  # Archive
    QTest.qWait(10)

    displayed = _displayed_names(home)
    assert "game.zip" in displayed
    assert "setup.exe" not in displayed


def test_category_filter_ui_empty_shows_filtered_empty(qapp):
    from app.ui.pages.home_page import HomePage

    home = HomePage()
    home.show()
    QTest.qWait(10)

    views = [_view("setup.exe", content_type="application/x-msdownload")]
    _populate(home, views)

    home._result_area._filter_combo.setCurrentIndex(5)  # Category
    QTest.qWait(10)
    home._result_area._category_combo.setCurrentIndex(2)  # Documentation (no match)
    QTest.qWait(10)

    assert home._result_area._filtered_empty.isVisible()
    assert not home._result_area._resource_list.isVisible()
