"""V1.9.0 Phase 4 — Selection + Filtering composition tests.

Validates the contract:
  * `select_visible(ids)` only adds the intersection with `eligible_ids`.
  * `deselect_visible(ids)` only removes the intersection with `selected_ids`.
  * Hidden selections are preserved across filter changes.
  * Filtering alone does not change selection state.
  * Idempotent repeated select/deselect operations.
  * Compatible with filtering and sorting composition.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.models import DownloadFile
from app.services.analysis_view import ResourceView
from app.services.categorization import ResourceCategory, categorize_resource
from app.services.filtering import FilterMode, ResourceFilter, apply_filter
from app.services.selection import (
    ResourceSelectionController,
    resource_id_for,
)
from app.services.sorting import SortMode, SortSpec, sort_resources


def _view(name: str, *, confidence: str = "medium", score: int = 50, size: int | None = None) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=f"https://example.com/{name}",
            size=size,
        ),
        confidence=confidence,
        score=score,
    )


@pytest.fixture
def resources() -> list[ResourceView]:
    return [
        _view("A.zip", confidence="high", score=90),
        _view("B.zip", confidence="medium", score=70),
        _view("C.zip", confidence="high", score=80),
        _view("D.zip", confidence="low", score=20),
        _view("E.zip", confidence="medium", score=60),
    ]


@pytest.fixture
def ids(resources) -> list[str]:
    return [resource_id_for(r) for r in resources]


def _visible_ids(resources_subset: list[ResourceView]) -> list[str]:
    return [resource_id_for(r) for r in resources_subset]


def test_select_visible_adds_all_visible(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    visible = _visible_ids([resources[0], resources[2]])

    ctrl.select_visible(visible)

    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[2])
    assert not ctrl.is_selected(resources[1])
    assert not ctrl.is_selected(resources[3])
    assert not ctrl.is_selected(resources[4])


def test_select_visible_preserves_hidden_selections(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)

    ctrl.toggle(resources[0])
    ctrl.toggle(resources[4])

    visible = _visible_ids([resources[1], resources[2]])
    ctrl.select_visible(visible)

    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[1])
    assert ctrl.is_selected(resources[2])
    assert not ctrl.is_selected(resources[3])
    assert ctrl.is_selected(resources[4])
    assert ctrl.state.count == 4


def test_deselect_visible_only_removes_visible(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()

    visible = _visible_ids([resources[1], resources[2]])
    ctrl.deselect_visible(visible)

    assert ctrl.is_selected(resources[0])
    assert not ctrl.is_selected(resources[1])
    assert not ctrl.is_selected(resources[2])
    assert ctrl.is_selected(resources[3])
    assert ctrl.is_selected(resources[4])
    assert ctrl.state.count == 3


def test_filter_change_does_not_alter_selection(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])
    ctrl.toggle(resources[4])
    before = {resource_id_for(r) for r in resources if ctrl.is_selected(resource_id_for(r))}

    high_only = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    high_ids = _visible_ids(high_only)
    ctrl.select_visible(high_ids)

    after = {resource_id_for(r) for r in resources if ctrl.is_selected(resource_id_for(r))}
    assert before <= after
    assert after >= {resource_id_for(resources[0]), resource_id_for(resources[2])}


def test_filter_then_deselect_visible_preserves_hidden(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()

    high_only = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    high_ids = _visible_ids(high_only)
    ctrl.deselect_visible(high_ids)

    assert not ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[1])
    assert not ctrl.is_selected(resources[2])
    assert ctrl.is_selected(resources[3])
    assert ctrl.is_selected(resources[4])


def test_select_visible_with_no_filter_selects_everything(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    visible = _visible_ids(resources)

    ctrl.select_visible(visible)

    assert ctrl.state.count == len(resources)
    assert ctrl.state.all_eligible_selected


def test_deselect_visible_with_no_filter_clears_everything(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()
    visible = _visible_ids(resources)

    ctrl.deselect_visible(visible)

    assert ctrl.state.count == 0


def test_select_visible_empty_does_nothing(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()

    ctrl.select_visible([])

    assert ctrl.state.count == len(resources)


def test_deselect_visible_empty_does_nothing(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()

    ctrl.deselect_visible([])

    assert ctrl.state.count == len(resources)


def test_select_visible_skips_non_eligible(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    visible = _visible_ids([resources[0], resources[2]])

    ctrl.select_visible(visible + ["not-eligible-id"])
    assert ctrl.state.count == 2


def test_deselect_visible_skips_non_selected(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])

    ctrl.deselect_visible(_visible_ids([resources[1], resources[2], resources[3]]))
    assert ctrl.state.count == 1
    assert ctrl.is_selected(resources[0])


def test_visible_selection_count(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])
    ctrl.toggle(resources[4])

    high_only = _visible_ids(apply_filter(resources, ResourceFilter(FilterMode.HIGH)))
    assert ctrl.visible_selection_count(high_only) == 2

    all_visible = _visible_ids(resources)
    assert ctrl.visible_selection_count(all_visible) == 3

    low_only = _visible_ids(apply_filter(resources, ResourceFilter(FilterMode.LOW)))
    assert ctrl.visible_selection_count(low_only) == 0


def test_multiple_filter_changes_preserve_selection(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])
    ctrl.toggle(resources[4])
    original = {resource_id_for(r) for r in resources if ctrl.is_selected(resource_id_for(r))}

    for mode in (FilterMode.HIGH, FilterMode.MEDIUM, FilterMode.LOW, FilterMode.ALL):
        visible = _visible_ids(apply_filter(resources, ResourceFilter(mode)))
        ctrl.select_visible(visible)

    current = {resource_id_for(r) for r in resources if ctrl.is_selected(resource_id_for(r))}
    assert original <= current
    assert current == {resource_id_for(r) for r in resources}


def test_sorting_after_filtering_does_not_change_selection(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])

    filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
    sorted_ids = _visible_ids(sorted_view)

    ctrl.select_visible(sorted_ids)
    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[2])


def test_filtering_after_sorting_does_not_change_selection(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[4])

    sorted_view = sort_resources(resources, SortSpec(SortMode.RECOMMENDED))
    visible = _visible_ids(apply_filter(sorted_view, ResourceFilter(FilterMode.MEDIUM)))
    ctrl.select_visible(visible)

    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[1])
    assert ctrl.is_selected(resources[4])
    assert not ctrl.is_selected(resources[3])


def test_selected_resources_retain_identity_after_filtering(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[1])
    ctrl.toggle(resources[3])

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    selected_from_high = ctrl.selected_resources(high)
    assert selected_from_high == []

    selected_from_all = ctrl.selected_resources(resources)
    assert resources[1] in selected_from_all
    assert resources[3] in selected_from_all


def test_hidden_selected_resources_remain_downloadable(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    high_ids = _visible_ids(high)
    ctrl.deselect_visible(high_ids)

    hidden_selected = [r for r in resources if not (resource_id_for(r) in set(high_ids)) and ctrl.is_selected(resource_id_for(r))]
    assert hidden_selected

    downloadable = ctrl.selected_resources(resources)
    assert downloadable == hidden_selected


def test_deterministic_selected_resource_ordering_after_filter_then_sort(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])

    sorted_view = sort_resources(resources, SortSpec(SortMode.RECOMMENDED))
    visible = _visible_ids(apply_filter(sorted_view, ResourceFilter(FilterMode.HIGH)))
    ctrl.select_visible(visible)

    selected = ctrl.selected_resources(resources)
    assert [r.file.name for r in selected] == ["A.zip", "C.zip"]


def test_select_all_visible_is_not_select_all_global(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible(_visible_ids(high))

    assert ctrl.state.count == 2
    assert not ctrl.state.all_eligible_selected


def test_deselect_all_visible_is_not_deselect_all_global(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()
    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.deselect_visible(_visible_ids(high))

    assert ctrl.state.count == 3
    assert ctrl.state.any_selected


def test_stale_ids_ignored(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()
    ctrl.deselect_visible(["stale-id-1", "stale-id-2"])
    assert ctrl.state.count == len(resources)


def test_empty_resource_collection():
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([])
    ctrl.select_visible([])
    ctrl.deselect_visible([])
    assert ctrl.state.count == 0
    assert ctrl.visible_selection_count([]) == 0


def test_repeated_select_visible_is_idempotent(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    visible = _visible_ids([resources[0], resources[2]])

    for _ in range(5):
        ctrl.select_visible(visible)

    assert ctrl.state.count == 2


def test_repeated_deselect_visible_is_idempotent(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()
    visible = _visible_ids([resources[0], resources[2]])

    for _ in range(5):
        ctrl.deselect_visible(visible)

    assert ctrl.state.count == 3


def test_filter_select_filter_select_preserves_intent(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible(_visible_ids(high))

    medium = apply_filter(resources, ResourceFilter(FilterMode.MEDIUM))
    ctrl.select_visible(_visible_ids(medium))

    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[1])
    assert ctrl.is_selected(resources[2])
    assert ctrl.is_selected(resources[4])


def test_filter_select_then_deselect_visible_is_symmetric(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.select_all()

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.deselect_visible(_visible_ids(high))
    assert ctrl.state.count == 3

    high_again = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible(_visible_ids(high_again))
    assert ctrl.state.count == 5


def test_multiple_filters_with_preserved_hidden_selections(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)

    ctrl.select_visible(_visible_ids(apply_filter(resources, ResourceFilter(FilterMode.HIGH))))
    ctrl.select_visible(_visible_ids(apply_filter(resources, ResourceFilter(FilterMode.LOW))))
    ctrl.select_visible(_visible_ids(apply_filter(resources, ResourceFilter(FilterMode.MEDIUM))))

    assert ctrl.state.count == 5
    assert ctrl.state.all_eligible_selected


def test_composition_with_categorization(resources, ids):
    """Sorting → filtering → select_visible → categorize — selections are
    independent of category, and categorization does not affect selection."""
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)

    sorted_view = sort_resources(resources, SortSpec(SortMode.RECOMMENDED))
    high = apply_filter(sorted_view, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible(_visible_ids(high))

    for r in high:
        assert isinstance(categorize_resource(r), ResourceCategory)

    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[2])
    assert ctrl.state.count == 2


def test_set_eligible_preserves_selection_intersection(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])
    ctrl.toggle(resources[4])

    ctrl.set_eligible(_visible_ids([resources[1], resources[2], resources[3]]))

    assert ctrl.is_selected(resources[2])
    assert not ctrl.is_selected(resources[0])
    assert not ctrl.is_selected(resources[4])


def test_select_visible_then_filter_then_select_visible(resources, ids):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible(ids)

    ctrl.select_visible(_visible_ids([resources[0], resources[1]]))
    assert ctrl.state.count == 2

    ctrl.select_visible(_visible_ids([resources[3], resources[4]]))
    assert ctrl.state.count == 4
    assert not ctrl.is_selected(resources[2])

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible(_visible_ids(high))
    assert ctrl.state.count == 5
