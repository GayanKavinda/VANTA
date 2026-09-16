"""V2.0 Phase 3.7 — Analysis Workflow & Resource Interaction UX tests.

Covers the Phase 3.7 deltas on top of the Phase 3.6 baseline:

* local resource search (`app.services.search`)
* selection summary computation (`app.services.selection`)
* integrated analysis workflow: search box, filtered-empty state,
  rich selection summary, and selection preservation across search/filter.

These tests verify observable behaviour, not implementation details.
"""
from __future__ import annotations

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QLabel

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import AnalysisViewModel, ResourceView
from app.services.filtering import FilterMode, ResourceFilter, apply_filter
from app.services.search import search_resources
from app.services.selection import (
    SelectionSummary,
    compute_selection_summary,
    format_selection_summary,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _view(
    name: str,
    *,
    confidence: str = ConfidenceLevel.HIGH,
    score: int = 80,
    size: int | None = None,
    content_type: str | None = None,
    url: str | None = None,
    mime_category: str = "",
    mime_media_type: str | None = None,
    duplicate: bool = False,
    element_type: str = "",
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=url or f"https://example.com/{name}",
            size=size,
            content_type=content_type,
        ),
        confidence=confidence,
        score=score,
        mime_category=mime_category,
        mime_media_type=mime_media_type,
        type_label=(mime_media_type or content_type or "").split(";")[0].strip() if content_type else "",
        duplicate_is_duplicate=duplicate,
        element_type=element_type,
    )


def _make_view_model(resources):
    return AnalysisViewModel(
        title="Test Page",
        source="https://example.com/page",
        status="ready",
        resources=resources,
        has_resources=bool(resources),
    )


SAMPLE_RESOURCES = [
    _view("setup.exe", size=5 * 1024 * 1024, content_type="application/x-msdownload",
          mime_category="installer"),
    _view("game.zip", size=10 * 1024 * 1024, content_type="application/zip",
          mime_category="archive"),
    _view("patch.zip", confidence=ConfidenceLevel.MEDIUM, score=60, size=None,
          content_type="application/zip", mime_category="archive", duplicate=True),
    _view("manual.pdf", confidence=ConfidenceLevel.LOW, score=20, size=2 * 1024 * 1024,
          content_type="application/pdf", mime_category="document"),
    _view("logo.png", confidence=ConfidenceLevel.LOW, size=None, content_type="image/png",
          mime_category="image", element_type="img"),
]


# ---------------------------------------------------------------------------
# Search service tests
# ---------------------------------------------------------------------------

def test_search_empty_query_returns_all_preserve_order():
    out = search_resources("", SAMPLE_RESOURCES)
    assert [v.file.name for v in out] == [v.file.name for v in SAMPLE_RESOURCES]
    assert out is not SAMPLE_RESOURCES


def test_search_whitespace_query_returns_all():
    out = search_resources("   ", SAMPLE_RESOURCES)
    assert len(out) == len(SAMPLE_RESOURCES)


def test_search_matches_filename_case_insensitive():
    out = search_resources("ZIP", SAMPLE_RESOURCES)
    assert {v.file.name for v in out} == {"game.zip", "patch.zip"}


def test_search_matches_url():
    out = search_resources("example.com/setup", SAMPLE_RESOURCES)
    assert [v.file.name for v in out] == ["setup.exe"]


def test_search_matches_content_type():
    out = search_resources("pdf", SAMPLE_RESOURCES)
    assert [v.file.name for v in out] == ["manual.pdf"]


def test_search_matches_mime_category():
    out = search_resources("image", SAMPLE_RESOURCES)
    assert [v.file.name for v in out] == ["logo.png"]


def test_search_no_match_returns_empty():
    out = search_resources("nonexistent-token", SAMPLE_RESOURCES)
    assert out == []


def test_search_does_not_mutate_input():
    original = [v.file.name for v in SAMPLE_RESOURCES]
    search_resources("zip", SAMPLE_RESOURCES)
    assert [v.file.name for v in SAMPLE_RESOURCES] == original


def test_search_returns_references_not_copies():
    out = search_resources("zip", SAMPLE_RESOURCES)
    assert out[0] is SAMPLE_RESOURCES[1]


def test_search_empty_resource_collection():
    assert search_resources("anything", []) == []


# ---------------------------------------------------------------------------
# Selection summary tests
# ---------------------------------------------------------------------------

def test_summary_counts_selected_resources():
    summary = compute_selection_summary(SAMPLE_RESOURCES)
    assert summary.count == 5


def test_summary_total_known_size_preserves_unknown():
    summary = compute_selection_summary(SAMPLE_RESOURCES)
    # setup.exe (5MB) + game.zip (10MB) + manual.pdf (2MB) known;
    # patch.zip and logo.png are unknown.
    assert summary.known_size_bytes == 5 * 1024 * 1024 + 10 * 1024 * 1024 + 2 * 1024 * 1024
    assert summary.unknown_size_count == 2


def test_summary_does_not_treat_unknown_as_zero():
    """Unknown sizes must not inflate/deflate the known total."""
    views = [
        _view("a.zip", size=None),
        _view("b.zip", size=100),
    ]
    summary = compute_selection_summary(views)
    assert summary.known_size_bytes == 100
    assert summary.unknown_size_count == 1


def test_summary_categories_represented():
    summary = compute_selection_summary(SAMPLE_RESOURCES)
    assert "Archive" in summary.categories
    assert "Installer" in summary.categories
    assert "Documentation" in summary.categories


def test_summary_categories_excludes_unknown_when_other_categories_present():
    # logo.png is "unknown" categorically (image), but setup.exe is Installer.
    summary = compute_selection_summary([_view("logo.png", mime_category="image"), _view("setup.exe")])
    assert "Installer" in summary.categories
    # No uncategorised-only label leak.
    assert summary.categories == ["Installer"]


def test_summary_duplicate_count():
    summary = compute_selection_summary(SAMPLE_RESOURCES)
    assert summary.duplicate_count == 1


def test_summary_empty_selection():
    summary = compute_selection_summary([])
    assert summary.count == 0
    assert summary.known_size_bytes == 0
    assert summary.unknown_size_count == 0
    assert summary.categories == []
    assert summary.duplicate_count == 0


def test_format_summary_empty():
    assert format_selection_summary(SelectionSummary(0, 0, 0, [], 0)) == "No resources selected"


def test_format_summary_singular():
    s = SelectionSummary(count=1, known_size_bytes=1024, unknown_size_count=0, categories=["Archive"], duplicate_count=0)
    text = format_selection_summary(s)
    assert "1 resource selected" in text
    assert "Total size" in text
    assert "Categories: Archive" in text


def test_format_summary_plural_with_unknown_size():
    s = SelectionSummary(count=3, known_size_bytes=3 * 1024 * 1024, unknown_size_count=2,
                         categories=["Archive", "Installer"], duplicate_count=1)
    text = format_selection_summary(s)
    assert "3 resources selected" in text
    assert "Total size: 3.0 MB + 2 unknown" in text
    assert "Categories: Archive, Installer" in text
    assert "Duplicates: 1" in text


def test_format_summary_omits_duplicate_line_when_none():
    s = SelectionSummary(count=2, known_size_bytes=1024, unknown_size_count=0, categories=["Archive"], duplicate_count=0)
    text = format_selection_summary(s)
    assert "Duplicates" not in text


# ---------------------------------------------------------------------------
# UI integration tests
# ---------------------------------------------------------------------------

def _displayed_ids(home_page) -> list[str]:
    return [row.resource_id for row in home_page._result_area._resource_rows]


@pytest.fixture
def home_page(qapp):
    from app.ui.pages.home_page import HomePage

    page = HomePage()
    page.show()
    QTest.qWait(10)
    return page


@pytest.fixture
def populated_home_page(home_page):
    vm = _make_view_model(SAMPLE_RESOURCES)
    home_page.show_analysis_view(vm)
    QTest.qWait(20)
    return home_page


# --- Search UI ------------------------------------------------------------

def test_search_box_filters_visible_resources(populated_home_page):
    home = populated_home_page

    assert len(_displayed_ids(home)) == 5

    home._result_area._search_input.setText("zip")
    QTest.qWait(10)

    displayed_names = [row._view.file.name for row in home._result_area._resource_rows]
    assert set(displayed_names) == {"game.zip", "patch.zip"}
    assert len(_displayed_ids(home)) == 2


def test_search_no_match_shows_filtered_empty_state(populated_home_page):
    home = populated_home_page

    home._result_area._search_input.setText("zzzzz")
    QTest.qWait(10)

    assert home._result_area._filtered_empty.isVisible()
    assert "No resources match" in home._result_area._filtered_empty_title.text()
    assert not home._result_area._resource_list.isVisible()


def test_search_filtered_empty_clears_on_new_query(populated_home_page):
    home = populated_home_page

    home._result_area._search_input.setText("zzzzz")
    QTest.qWait(10)
    assert home._result_area._filtered_empty.isVisible()

    home._result_area._search_input.setText("zip")
    QTest.qWait(10)

    assert not home._result_area._filtered_empty.isVisible()
    assert home._result_area._resource_list.isVisible()


# --- Filter empty state ---------------------------------------------------

def test_filter_empty_file_type_shows_filtered_empty_state(populated_home_page):
    home = populated_home_page

    # "File Type" is index 4 in the filter combo.
    home._result_area._filter_combo.setCurrentIndex(4)
    QTest.qWait(10)
    # Select a file type with no matches ("rar").
    home._result_area._file_type_combo.setCurrentIndex(1)
    QTest.qWait(10)

    assert home._result_area._filtered_empty.isVisible()
    assert not home._result_area._resource_list.isVisible() or len(_displayed_ids(home)) == 0


def test_filtered_empty_clear_filters_restores_list(populated_home_page):
    home = populated_home_page

    # Nothing matches "zzz".
    home._result_area._search_input.setText("zzz")
    QTest.qWait(10)
    assert home._result_area._filtered_empty.isVisible()

    # Click the "Clear Filters" button in the empty state.
    assert home._result_area._clear_filters_btn is not None
    home._result_area._clear_filters_btn.click()
    QTest.qWait(10)

    assert not home._result_area._filtered_empty.isVisible()
    assert len(_displayed_ids(home)) == 5


# --- Selection summary UI -------------------------------------------------

def test_selection_summary_shows_size_and_categories(populated_home_page):
    home = populated_home_page

    # Select setup.exe and game.zip (both known size, distinct categories).
    for row in home._result_area._resource_rows:
        if row._view.file.name in ("setup.exe", "game.zip"):
            home._selection.toggle(row.resource_id)
    home._result_area.refresh_resources(home._current_view, home._selection)
    QTest.qWait(10)

    text = home._result_area._selection_summary.text()
    assert "2 resources selected" in text
    assert "Total size: 15.0 MB" in text
    assert "Categories:" in text
    assert "Installer" in text
    assert "Archive" in text


def test_selection_summary_shows_unknown_size_and_duplicates(populated_home_page):
    home = populated_home_page

    for row in home._result_area._resource_rows:
        if row._view.file.name in ("logo.png", "patch.zip"):
            home._selection.toggle(row.resource_id)
    home._result_area.refresh_resources(home._current_view, home._selection)
    QTest.qWait(10)

    text = home._result_area._selection_summary.text()
    assert "2 resources selected" in text
    assert "2 unknown" in text
    assert "Duplicates: 1" in text


def test_no_selection_shows_count_not_rich_summary(populated_home_page):
    home = populated_home_page

    text = home._result_area._selection_summary.text()
    assert "0 of 5 selected" in text
    assert "Total size" not in text


# --- Selection preservation ------------------------------------------------

def test_selection_survives_search(populated_home_page):
    home = populated_home_page

    # Select a resource, then filter it out via search.
    rows = home._result_area._resource_rows
    target = [r for r in rows if r._view.file.name == "manual.pdf"][0]
    home._selection.toggle(target.resource_id)
    home._result_area.refresh_resources(home._current_view, home._selection)
    QTest.qWait(10)

    assert home._selection.state.count == 1

    home._result_area._search_input.setText("zip")
    QTest.qWait(10)

    # manual.pdf is hidden but still selected in the model.
    assert home._selection.is_selected(target.resource_id)
    assert home._selection.state.count == 1


def test_selection_survives_filter_change(populated_home_page):
    home = populated_home_page

    for r in home._result_area._resource_rows:
        home._selection.toggle(r.resource_id)
    home._result_area.refresh_resources(home._current_view, home._selection)
    QTest.qWait(10)
    assert home._selection.state.count == 5

    home._result_area._filter_combo.setCurrentIndex(1)  # HIGH
    QTest.qWait(10)

    assert home._selection.state.count == 5


# --- Bulk download still routes through review (no direct downloader) ------

def test_download_selected_emits_signal_without_queueing(home_page):
    """Download Selected must emit downloads_selected; it must NOT call the
    download manager directly from the UI."""
    vm = _make_view_model(SAMPLE_RESOURCES[:2])
    home_page.set_current_url_for_result("https://example.com/page")
    home_page.show_analysis_view(vm)
    QTest.qWait(20)

    # Select All via the toolbar button so the selection + toolbar sync.
    home_page._result_area._select_all_btn.click()
    QTest.qWait(10)

    emitted = []

    def _capture(source_url, selected_views):
        emitted.append((source_url, selected_views))

    home_page.downloads_selected.connect(_capture)
    home_page._result_area._download_selected_btn.click()
    QTest.qWait(10)

    assert len(emitted) == 1
    source_url, selected_views = emitted[0]
    assert source_url == "https://example.com/page"
    assert len(selected_views) == 2
    assert {v.file.name for v in selected_views} == {"setup.exe", "game.zip"}


def test_download_selected_disabled_when_nothing_selected(populated_home_page):
    home = populated_home_page
    assert not home._result_area._download_selected_btn.isEnabled()


# --- Per-row actions: duplicate badge + copy URL -------------------------

def _find_row(home, name):
    for row in home._result_area._resource_rows:
        if row._view.file.name == name:
            return row
    return None


def _find_copy_button(row):
    for btn in row.findChildren(QPushButton):
        if btn.text() == "Copy URL":
            return btn
    return None


def test_resource_row_shows_duplicate_badge_for_duplicates(populated_home_page):
    home = populated_home_page
    patch_row = _find_row(home, "patch.zip")
    assert patch_row is not None
    badge = patch_row.findChild(QLabel, "duplicate_badge")
    assert badge is not None
    assert "Duplicate" in badge.text()


def test_resource_row_hides_duplicate_badge_for_normal(populated_home_page):
    home = populated_home_page
    setup_row = _find_row(home, "setup.exe")
    assert setup_row is not None
    assert setup_row.findChild(QLabel, "duplicate_badge") is None


def test_resource_row_copy_url_copies_to_clipboard(populated_home_page):
    home = populated_home_page
    patch_row = _find_row(home, "patch.zip")
    copy_btn = _find_copy_button(patch_row)
    assert copy_btn is not None

    clipboard = QApplication.clipboard()
    clipboard.clear()
    copy_btn.click()
    QTest.qWait(10)

    assert clipboard.text() == "https://example.com/patch.zip"


def test_resource_row_copy_url_emits_resource_url(populated_home_page):
    home = populated_home_page
    patch_row = _find_row(home, "patch.zip")

    captured = []
    home._result_area._on_resource_copy_url = lambda url: captured.append(url)
    # Reconnect the row signal to our capture handler (the row was already
    # wired during _render_resources; we override the slot to observe the emit).
    patch_row.copy_url_clicked.connect(lambda url: captured.append(url))

    copy_btn = _find_copy_button(patch_row)
    copy_btn.click()
    QTest.qWait(10)

    assert "https://example.com/patch.zip" in captured


# --- Category filter (pure service) -------------------------------------

def test_category_filter_matches_archive():
    out = apply_filter(SAMPLE_RESOURCES, ResourceFilter(FilterMode.CATEGORY, category="archive"))
    assert [v.file.name for v in out] == ["game.zip"]


def test_category_filter_is_case_insensitive():
    out = apply_filter(SAMPLE_RESOURCES, ResourceFilter(FilterMode.CATEGORY, category="ARCHIVE"))
    assert [v.file.name for v in out] == ["game.zip"]


def test_category_filter_excludes_others():
    out = apply_filter(SAMPLE_RESOURCES, ResourceFilter(FilterMode.CATEGORY, category="archive"))
    names = {v.file.name for v in out}
    assert "logo.png" not in names
    assert "setup.exe" not in names


def test_category_filter_multiple_categories():
    out = apply_filter(SAMPLE_RESOURCES, ResourceFilter(FilterMode.CATEGORY, category="installer"))
    assert [v.file.name for v in out] == ["setup.exe"]


def test_category_filter_does_not_mutate_input():
    snapshot = [v.file.name for v in SAMPLE_RESOURCES]
    apply_filter(SAMPLE_RESOURCES, ResourceFilter(FilterMode.CATEGORY, category="archive"))
    assert [v.file.name for v in SAMPLE_RESOURCES] == snapshot


def test_category_filter_requires_category():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.CATEGORY)


def test_category_filter_rejects_file_type_param():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.CATEGORY, category="archive", file_type="zip")


def test_file_type_filter_rejects_category_param():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.FILE_TYPE, category="archive", file_type="zip")


def test_all_filter_rejects_category_param():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.ALL, category="archive")


def test_category_filter_empty_result():
    out = apply_filter(SAMPLE_RESOURCES, ResourceFilter(FilterMode.CATEGORY, category="video"))
    assert out == []


# --- Category filter UI --------------------------------------------------

def test_category_filter_ui_changes_visible_resources(populated_home_page):
    home = populated_home_page

    home._result_area._filter_combo.setCurrentIndex(5)  # Category
    QTest.qWait(10)

    # "Archive" is index 0 in the category combo.
    home._result_area._category_combo.setCurrentIndex(0)
    QTest.qWait(10)

    displayed = [row._view.file.name for row in home._result_area._resource_rows]
    assert displayed == ["game.zip"]


def test_category_filter_switched_to_all_restores_list(populated_home_page):
    home = populated_home_page

    home._result_area._filter_combo.setCurrentIndex(5)
    QTest.qWait(10)
    home._result_area._category_combo.setCurrentIndex(0)
    QTest.qWait(10)
    assert len(_displayed_ids(home)) == 1

    home._result_area._filter_combo.setCurrentIndex(0)  # All
    QTest.qWait(10)
    assert len(_displayed_ids(home)) == 5
