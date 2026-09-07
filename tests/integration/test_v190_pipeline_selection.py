from __future__ import annotations

import pytest

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import ResourceView
from app.services.filtering import (
    FilterMode,
    ResourceFilter,
    apply_filter,
)
from app.services.grouping import group_resources
from app.services.selection import (
    ResourceSelectionController,
    resource_id_for,
)
from app.services.sorting import (
    SortMode,
    SortSpec,
    sort_resources,
)


def make_view(
    name: str,
    confidence: ConfidenceLevel,
    score: int,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=f"https://example.com/{name}",
        ),
        confidence=confidence,
        score=score,
    )


@pytest.mark.integration
def test_selection_persists_across_filter_change():
    """Filtering changes visibility, never selection state."""
    resources = [
        make_view("setup.exe", ConfidenceLevel.HIGH, 90),
        make_view("readme.txt", ConfidenceLevel.LOW, 20),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[1])

    assert ctrl.state.count == 2

    apply_filter(resources, ResourceFilter(FilterMode.HIGH))

    assert ctrl.state.count == 2
    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[1])


@pytest.mark.integration
def test_selection_persists_across_sort_change():
    """Sorting changes display order, never selection state."""
    resources = [
        make_view("a.exe", ConfidenceLevel.HIGH, 50),
        make_view("b.exe", ConfidenceLevel.HIGH, 90),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.toggle(resources[0])

    sort_resources(resources, SortSpec(SortMode.SCORE_DESC))

    assert ctrl.state.count == 1
    assert ctrl.is_selected(resources[0])


@pytest.mark.integration
def test_selection_persists_across_group_change():
    """Grouping is presentation-only; selection is untouched."""
    resources = [
        make_view("setup.exe", ConfidenceLevel.HIGH, 90),
        make_view("readme.txt", ConfidenceLevel.HIGH, 40),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.toggle(resources[0])

    group_resources(resources)

    assert ctrl.state.count == 1
    assert ctrl.is_selected(resources[0])


@pytest.mark.integration
def test_selection_persists_across_full_pipeline():
    """Filter → Sort → Group must not alter selection."""
    resources = [
        make_view("setup.exe", ConfidenceLevel.HIGH, 90),
        make_view("game.zip", ConfidenceLevel.HIGH, 80),
        make_view("readme.txt", ConfidenceLevel.LOW, 20),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])

    filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.SCORE_DESC))
    group_resources(sorted_view)

    assert ctrl.state.count == 2
    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[2])
    assert not ctrl.is_selected(resources[1])


@pytest.mark.integration
def test_select_visible_then_change_filter_preserves_hidden():
    """Select visible, then change filter — hidden selections remain."""
    resources = [
        make_view("a.exe", ConfidenceLevel.HIGH, 90),
        make_view("b.exe", ConfidenceLevel.LOW, 30),
        make_view("c.exe", ConfidenceLevel.HIGH, 80),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible([resource_id_for(r) for r in high])
    assert ctrl.state.count == 2

    all_view = apply_filter(resources, ResourceFilter(FilterMode.ALL))
    ctrl.select_visible([resource_id_for(r) for r in all_view])
    assert ctrl.state.count == 3


@pytest.mark.integration
def test_deselect_visible_then_change_filter_preserves_hidden():
    """Deselect visible, then change filter — hidden selections remain."""
    resources = [
        make_view("a.exe", ConfidenceLevel.HIGH, 90),
        make_view("b.exe", ConfidenceLevel.LOW, 30),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.select_all()

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.deselect_visible([resource_id_for(r) for r in high])
    assert ctrl.is_selected(resources[1])
    assert not ctrl.is_selected(resources[0])


@pytest.mark.integration
def test_filter_select_filter_select_accumulates():
    """Multiple filter+select cycles accumulate correctly."""
    resources = [
        make_view("a.exe", ConfidenceLevel.HIGH, 90),
        make_view("b.exe", ConfidenceLevel.MEDIUM, 70),
        make_view("c.exe", ConfidenceLevel.LOW, 30),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible([resource_id_for(r) for r in high])

    medium = apply_filter(resources, ResourceFilter(FilterMode.MEDIUM))
    ctrl.select_visible([resource_id_for(r) for r in medium])

    low = apply_filter(resources, ResourceFilter(FilterMode.LOW))
    ctrl.select_visible([resource_id_for(r) for r in low])

    assert ctrl.state.count == 3


@pytest.mark.integration
def test_repeated_analysis_recomputes_eligible_preserving_intersection():
    """A new analysis re-sets eligible ids; selections that are still
    eligible remain; stale selections are dropped."""
    first = [
        make_view("a.exe", ConfidenceLevel.HIGH, 90),
        make_view("b.exe", ConfidenceLevel.HIGH, 80),
    ]
    second = [
        make_view("a.exe", ConfidenceLevel.HIGH, 90),
        make_view("c.exe", ConfidenceLevel.HIGH, 70),
    ]

    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in first])
    ctrl.select_all()
    assert ctrl.state.count == 2

    ctrl.set_eligible([resource_id_for(r) for r in second])
    assert ctrl.is_selected(second[0])
    assert not ctrl.is_selected(second[1])
    assert ctrl.state.count == 1


@pytest.mark.integration
def test_selected_resources_after_pipeline_returns_only_visible():
    """`selected_resources(resources)` returns only the selected ones,
    regardless of filter/sort state."""
    resources = [
        make_view("a.exe", ConfidenceLevel.HIGH, 90),
        make_view("b.exe", ConfidenceLevel.HIGH, 80),
        make_view("c.exe", ConfidenceLevel.LOW, 30),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])

    sorted_view = sort_resources(resources, SortSpec(SortMode.SCORE_DESC))
    selected = ctrl.selected_resources(sorted_view)

    assert resources[0] in selected
    assert resources[2] in selected
    assert resources[1] not in selected