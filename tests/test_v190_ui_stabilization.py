"""V1.9.0 stabilization — UI regression tests for filter/sort rendering.

These tests verify the fix for the bug where `_on_filter_changed` and
`_on_sort_changed` updated the **state** but did not trigger a **view
rebuild**. They exercise the real `HomePage` widget using PySide6 in
offscreen mode, driving the actual combo-box signal chain.

Coverage:

* `test_filter_change_rebuilds_visible_resources` — changing the filter
  combo actually reduces the visible resource list. (This is the
  regression test that would FAIL before the fix.)
* `test_filter_change_all_to_high_to_low_to_all` — filter changes are
  reversible and rebuild every time.
* `test_sort_change_reorders_visible_resources` — changing the sort
  combo actually reorders the displayed resources.
* `test_sort_change_score_desc_reorders` — verifies the exact sort
  output against a pure-pipeline expectation.
* `test_filter_change_preserves_selection` — a selection hidden by a
  filter re-appears selected when the filter is restored.
* `test_sort_change_preserves_selection` — selection survives a sort
  reorder.
* `test_new_analysis_resets_controls` — `show_analysis_view` resets
  filter/sort to ALL / RECOMMENDED.
* `test_new_analysis_clears_selection` — a new analysis clears the
  selection.
* `test_refresh_resources_does_not_reset_selection_eligibility` —
  `refresh_resources` does NOT call `set_eligible`, so the eligibility
  set is untouched.
* `test_filter_change_then_select_visible_then_restore` — select
  visible under a filter, then restore the filter: hidden selections are
  preserved.
* `test_programmatic_checkbox_set_does_not_toggle_selection` —
  `set_checked` must not emit `selection_changed` (no feedback loop
  that removes just-added selections).
"""
from __future__ import annotations

import pytest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import AnalysisViewModel, ResourceView
from app.services.filtering import FilterMode, ResourceFilter, apply_filter
from app.services.grouping import group_resources
from app.services.selection import ResourceSelectionController, resource_id_for
from app.services.sorting import SortMode, SortSpec, sort_resources


def _view(
    name: str,
    *,
    confidence: str = ConfidenceLevel.HIGH,
    score: int = 80,
    size: int | None = None,
    content_type: str | None = None,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=f"https://example.com/{name}",
            size=size,
            content_type=content_type,
        ),
        confidence=confidence,
        score=score,
    )


def _make_view_model() -> tuple[AnalysisViewModel, list[ResourceView]]:
    """Six resources across three confidence levels.

    Scores are deliberately arranged so that RECOMMENDED (confidence-first)
    differs from pure SCORE_DESC and FILENAME_ASC, even after grouping.
    """
    resources = [
        _view("z_installer.exe", confidence=ConfidenceLevel.HIGH, score=90,
              content_type="application/octet-stream"),
        _view("a_installer.exe", confidence=ConfidenceLevel.HIGH, score=60,
              content_type="application/octet-stream"),
        _view("b_installer.exe", confidence=ConfidenceLevel.MEDIUM, score=80,
              content_type="application/octet-stream"),
        _view("c_installer.exe", confidence=ConfidenceLevel.MEDIUM, score=50,
              content_type="application/octet-stream"),
        _view("notes.txt", confidence=ConfidenceLevel.LOW, score=40,
              content_type="text/plain"),
        _view("readme.txt", confidence=ConfidenceLevel.LOW, score=30,
              content_type="text/plain"),
    ]
    vm = AnalysisViewModel(
        title="Test Page",
        source="https://example.com/page",
        status="ready",
        resources=resources,
        has_resources=True,
    )
    return vm, resources


def _displayed_ids(home_page) -> list[str]:
    return [row.resource_id for row in home_page._result_area._resource_rows]


def _displayed_names(home_page) -> list[str]:
    return [row._view.file.name for row in home_page._result_area._resource_rows]


# Combo-box indices for the filter combo (must stay in sync with _build_control_bar)
ALL_IDX = 0
HIGH_IDX = 1
MEDIUM_IDX = 2
LOW_IDX = 3

# Combo-box indices for the sort combo
SORT_RECOMMENDED = 0
SORT_SCORE_DESC = 2
SORT_FILENAME_ASC = 5


@pytest.fixture
def home_page(qapp):
    from app.ui.pages.home_page import HomePage

    page = HomePage()
    page.show()
    QTest.qWait(10)
    return page


@pytest.fixture
def populated_home_page(home_page):
    vm, resources = _make_view_model()
    home_page.show_analysis_view(vm)
    QTest.qWait(20)
    return home_page, resources


# ---------------------------------------------------------------------------
# Filter rendering
# ---------------------------------------------------------------------------

def test_filter_change_rebuilds_visible_resources(populated_home_page):
    home, resources = populated_home_page

    assert len(_displayed_ids(home)) == 6

    home._result_area._filter_combo.setCurrentIndex(HIGH_IDX)
    QTest.qWait(10)

    high_ids = {resource_id_for(resources[0]), resource_id_for(resources[1])}
    displayed = set(_displayed_ids(home))
    assert displayed == high_ids
    assert len(displayed) == 2


def test_filter_change_all_to_high_to_low_to_all(populated_home_page):
    home, resources = populated_home_page

    assert len(_displayed_ids(home)) == 6

    home._result_area._filter_combo.setCurrentIndex(HIGH_IDX)
    QTest.qWait(10)
    assert len(_displayed_ids(home)) == 2

    home._result_area._filter_combo.setCurrentIndex(LOW_IDX)
    QTest.qWait(10)
    assert len(_displayed_ids(home)) == 2

    home._result_area._filter_combo.setCurrentIndex(ALL_IDX)
    QTest.qWait(10)
    assert len(_displayed_ids(home)) == 6


# ---------------------------------------------------------------------------
# Sort rendering
# ---------------------------------------------------------------------------

def test_sort_change_reorders_visible_resources(populated_home_page):
    home, resources = populated_home_page

    initial = _displayed_ids(home)

    home._result_area._sort_combo.setCurrentIndex(SORT_FILENAME_ASC)
    QTest.qWait(10)

    after = _displayed_ids(home)

    assert set(initial) == set(after)
    assert initial != after


def test_sort_change_score_desc_reorders(populated_home_page):
    home, resources = populated_home_page

    initial = _displayed_names(home)

    home._result_area._sort_combo.setCurrentIndex(SORT_SCORE_DESC)
    QTest.qWait(10)

    after = _displayed_names(home)

    assert set(initial) == set(after)
    assert initial != after

    filtered = apply_filter(resources, ResourceFilter(FilterMode.ALL))
    sorted_v = sort_resources(filtered, SortSpec(SortMode.SCORE_DESC))
    grouped = group_resources(sorted_v)
    expected = [r.file.name for r in grouped.main + grouped.optional + grouped.other]
    assert after == expected


# ---------------------------------------------------------------------------
# Selection preservation across filter/sort changes
# ---------------------------------------------------------------------------

def test_filter_change_preserves_selection(populated_home_page):
    home, resources = populated_home_page

    low_id = resource_id_for(resources[5])  # readme.txt, LOW confidence
    assert not home._selection.is_selected(low_id)

    home._selection.toggle(low_id)
    assert home._selection.is_selected(low_id)

    # Filter to HIGH — the low-confidence resource disappears
    home._result_area._filter_combo.setCurrentIndex(HIGH_IDX)
    QTest.qWait(10)

    displayed = set(_displayed_ids(home))
    assert low_id not in displayed
    assert home._selection.is_selected(low_id)

    # Restore ALL — the resource reappears, checkbox checked
    home._result_area._filter_combo.setCurrentIndex(ALL_IDX)
    QTest.qWait(10)

    displayed = _displayed_ids(home)
    assert low_id in displayed

    for row in home._result_area._resource_rows:
        if row.resource_id == low_id:
            assert row._checkbox.isChecked()
            return
    raise AssertionError("Low-confidence resource not found after restoring ALL filter")


def test_sort_change_preserves_selection(populated_home_page):
    home, resources = populated_home_page

    for r in resources:
        home._selection.toggle(resource_id_for(r))
    assert home._selection.state.count == 6

    home._result_area._sort_combo.setCurrentIndex(SORT_FILENAME_ASC)
    QTest.qWait(10)

    assert home._selection.state.count == 6

    for row in home._result_area._resource_rows:
        assert home._selection.is_selected(row.resource_id)
        assert row._checkbox.isChecked()


# ---------------------------------------------------------------------------
# New-analysis control reset
# ---------------------------------------------------------------------------

def test_new_analysis_resets_controls(populated_home_page):
    home, resources = populated_home_page

    home._result_area._filter_combo.setCurrentIndex(HIGH_IDX)
    home._result_area._sort_combo.setCurrentIndex(SORT_FILENAME_ASC)
    QTest.qWait(10)

    assert home._result_area._filter_spec.mode is FilterMode.HIGH
    assert home._result_area._sort_spec.mode is SortMode.FILENAME_ASC

    home.show_analysis_view(
        AnalysisViewModel(
            title="Second Page",
            source="https://example.com/page2",
            status="ready",
            resources=[_view("new.zip", confidence=ConfidenceLevel.HIGH, score=99)],
            has_resources=True,
        )
    )
    QTest.qWait(20)

    assert home._result_area._filter_spec.mode is FilterMode.ALL
    assert home._result_area._sort_spec.mode is SortMode.RECOMMENDED
    assert home._result_area._filter_combo.currentIndex() == 0
    assert home._result_area._sort_combo.currentIndex() == 0
    assert not home._result_area._file_type_combo.isEnabled()


def test_new_analysis_clears_selection(populated_home_page):
    home, resources = populated_home_page

    for r in resources:
        home._selection.toggle(resource_id_for(r))
    assert home._selection.state.count == 6

    home.show_analysis_view(
        AnalysisViewModel(
            title="Second Page",
            source="https://example.com/page2",
            status="ready",
            resources=[_view("new.zip", confidence=ConfidenceLevel.HIGH, score=99)],
            has_resources=True,
        )
    )
    QTest.qWait(20)

    assert home._selection.state.count == 0


def test_refresh_resources_does_not_reset_selection_eligibility(populated_home_page):
    home, resources = populated_home_page

    initial_eligible = set(home._selection.state.eligible_ids)
    assert initial_eligible == {resource_id_for(r) for r in resources}

    home._result_area._filter_combo.setCurrentIndex(HIGH_IDX)
    QTest.qWait(10)

    assert set(home._selection.state.eligible_ids) == initial_eligible


def test_filter_change_then_select_visible_then_restore(populated_home_page):
    home, resources = populated_home_page

    home._result_area._filter_combo.setCurrentIndex(HIGH_IDX)
    QTest.qWait(10)

    visible = set(_displayed_ids(home))
    assert len(visible) == 2

    visible_ids = list(visible)
    home._selection.select_visible(visible_ids)
    assert home._selection.state.count == 2

    home._result_area._filter_combo.setCurrentIndex(ALL_IDX)
    QTest.qWait(10)

    assert home._selection.state.count == 2
    assert len(_displayed_ids(home)) == 6


def test_programmatic_checkbox_set_does_not_toggle_selection(populated_home_page):
    home, resources = populated_home_page

    all_ids = [resource_id_for(r) for r in resources]
    home._selection.set_eligible(all_ids)
    home._selection.select_all()
    QTest.qWait(10)
    assert home._selection.state.count == 6

    home._result_area.refresh_resources(home._current_view, home._selection)
    QTest.qWait(10)

    assert home._selection.state.count == 6
    for row in home._result_area._resource_rows:
        assert row._checkbox.isChecked()


