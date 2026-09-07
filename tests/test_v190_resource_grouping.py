"""V1.9.0 Phase 5 — Resource grouping tests.

Validates the grouping contract:
  * Pure Python, no I/O.
  * Does not mutate the input list.
  * Does not reorder resources within a group.
  * Every input resource appears in exactly one output group.
  * No resources lost or duplicated.
  * Returns references to the original `ResourceView` instances.
  * Selection state is independent of grouping.
  * Composes with filtering, sorting, and categorization.
"""
from __future__ import annotations

import pytest

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import ResourceView
from app.services.categorization import ResourceCategory
from app.services.filtering import FilterMode, ResourceFilter, apply_filter
from app.services.grouping import (
    GroupedResources,
    ResourceGroup,
    group_resources,
)
from app.services.selection import ResourceSelectionController, resource_id_for
from app.services.sorting import SortMode, SortSpec, sort_resources


def _view(
    name: str,
    *,
    confidence: str = "medium",
    score: int = 50,
    content_type: str | None = None,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=f"https://example.com/{name}",
            content_type=content_type,
        ),
        confidence=confidence,
        score=score,
    )


def test_main_installer_high_confidence():
    v = _view("setup.exe", confidence=ConfidenceLevel.HIGH, score=90)
    grouped = group_resources([v])
    assert grouped.main == [v]
    assert grouped.optional == []
    assert grouped.other == []


def test_main_archive():
    v = _view("game.zip", confidence=ConfidenceLevel.MEDIUM, score=80)
    grouped = group_resources([v])
    assert grouped.main == [v]


def test_main_multipart():
    parts = [
        _view("game.part1.rar", confidence=ConfidenceLevel.HIGH, score=80),
        _view("game.part2.rar", confidence=ConfidenceLevel.HIGH, score=80),
    ]
    grouped = group_resources(parts)
    assert grouped.main == parts
    assert grouped.optional == []
    assert grouped.other == []


def test_optional_patch():
    v = _view("game_patch_v1.2.exe", confidence=ConfidenceLevel.HIGH, score=70)
    grouped = group_resources([v])
    assert grouped.optional == [v]
    assert grouped.main == []


def test_optional_low_confidence_installer():
    v = _view("launcher.exe", confidence=ConfidenceLevel.LOW, score=30)
    grouped = group_resources([v])
    assert grouped.optional == [v]


def test_other_documentation():
    v = _view("readme.txt", confidence=ConfidenceLevel.HIGH, score=90)
    grouped = group_resources([v])
    assert grouped.other == [v]
    assert grouped.main == []
    assert grouped.optional == []


def test_other_unknown():
    v = _view("payload.iso", confidence=ConfidenceLevel.MEDIUM, score=50)
    grouped = group_resources([v])
    assert grouped.other == [v]


def test_other_rejected_confidence_even_for_installer():
    v = _view("setup.exe", confidence=ConfidenceLevel.REJECTED, score=10)
    grouped = group_resources([v])
    assert grouped.other == [v]
    assert grouped.main == []


def test_main_installer_medium_confidence():
    v = _view("setup.exe", confidence=ConfidenceLevel.MEDIUM, score=70)
    grouped = group_resources([v])
    assert grouped.main == [v]


def test_optional_unknown_but_high_score_and_confidence():
    v = _view("payload.bin", confidence=ConfidenceLevel.HIGH, score=80)
    grouped = group_resources([v])
    assert grouped.optional == [v]


def test_every_category_is_handled():
    """Every `ResourceCategory` value must be assigned to some group."""
    samples = [
        _view("setup.exe", confidence="high"),
        _view("game.zip", confidence="high"),
        _view("game.part1.rar", confidence="high"),
        _view("patch.exe", confidence="high"),
        _view("readme.txt", confidence="high"),
        _view("payload.iso", confidence="low"),
    ]
    grouped = group_resources(samples)
    assert grouped.total() == len(samples)
    assert set(grouped.by_group().keys()) == set(ResourceGroup)


def test_empty_input_returns_empty_groups():
    grouped = group_resources([])
    assert grouped == GroupedResources(main=[], optional=[], other=[])
    assert grouped.total() == 0


def test_single_resource():
    v = _view("only.zip")
    grouped = group_resources([v])
    assert grouped.total() == 1
    assert grouped.main == [v]


def test_mixed_resources_partition():
    resources = [
        _view("setup.exe", confidence="high", score=90),
        _view("game.zip", confidence="high", score=80),
        _view("game.part1.rar", confidence="high", score=80),
        _view("patch.exe", confidence="high", score=70),
        _view("launcher_low.exe", confidence="low", score=30),
        _view("readme.txt", confidence="high", score=90),
        _view("payload.iso", confidence="medium", score=50),
    ]
    grouped = group_resources(resources)

    main_names = [r.file.name for r in grouped.main]
    optional_names = [r.file.name for r in grouped.optional]
    other_names = [r.file.name for r in grouped.other]

    assert "setup.exe" in main_names
    assert "game.zip" in main_names
    assert "game.part1.rar" in main_names
    assert "patch.exe" in optional_names
    assert "launcher_low.exe" in optional_names
    assert "readme.txt" in other_names
    assert "payload.iso" in other_names


def test_input_order_preserved_within_each_group():
    resources = [
        _view("z-setup.exe", confidence="high", score=10),
        _view("a-readme.txt", confidence="high", score=90),
        _view("m-game.zip", confidence="high", score=50),
        _view("b-patch.exe", confidence="high", score=80),
    ]
    grouped = group_resources(resources)

    assert [r.file.name for r in grouped.main] == ["z-setup.exe", "m-game.zip"]
    assert [r.file.name for r in grouped.optional] == ["b-patch.exe"]
    assert [r.file.name for r in grouped.other] == ["a-readme.txt"]


def test_no_resources_lost():
    resources = [_view(f"file_{i}.zip", confidence="high", score=80) for i in range(20)]
    grouped = group_resources(resources)
    assert grouped.total() == len(resources)
    seen = set()
    for r in grouped.main:
        assert id(r) not in seen
        seen.add(id(r))
    for r in grouped.optional:
        assert id(r) not in seen
        seen.add(id(r))
    for r in grouped.other:
        assert id(r) not in seen
        seen.add(id(r))


def test_no_resources_duplicated_across_groups():
    resources = [
        _view("setup.exe", confidence="high"),
        _view("readme.txt", confidence="high"),
        _view("patch.exe", confidence="high"),
    ]
    grouped = group_resources(resources)
    all_output = grouped.main + grouped.optional + grouped.other
    input_ids = {id(r) for r in resources}
    output_ids = {id(r) for r in all_output}
    assert input_ids == output_ids
    assert len(all_output) == len(set(id(r) for r in all_output))


def test_returns_same_resource_instances():
    v1 = _view("setup.exe", confidence="high")
    v2 = _view("readme.txt", confidence="high")
    grouped = group_resources([v1, v2])
    assert grouped.main[0] is v1
    assert grouped.other[0] is v2


def test_does_not_mutate_input_list():
    resources = [
        _view("setup.exe", confidence="high"),
        _view("readme.txt", confidence="high"),
    ]
    snapshot_ids = [id(r) for r in resources]
    _ = group_resources(resources)
    assert [id(r) for r in resources] == snapshot_ids


def test_deterministic_output():
    resources = [
        _view("setup.exe", confidence="high"),
        _view("readme.txt", confidence="high"),
        _view("patch.exe", confidence="high"),
    ]
    a = group_resources(resources)
    b = group_resources(resources)
    c = group_resources(resources)
    assert a == b == c


def test_grouped_resources_equals_sum_of_lengths():
    resources = [
        _view("a.exe", confidence="high"),
        _view("b.zip", confidence="high"),
        _view("c.txt", confidence="high"),
        _view("d.iso", confidence="medium"),
        _view("e.patch.exe", confidence="high"),
    ]
    grouped = group_resources(resources)
    assert len(grouped.main) + len(grouped.optional) + len(grouped.other) == len(resources)


def test_composition_after_filtering():
    resources = [
        _view("setup.exe", confidence="high"),
        _view("game.zip", confidence="high"),
        _view("readme.txt", confidence="high"),
    ]
    filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    grouped = group_resources(filtered)
    assert grouped.main == [resources[0], resources[1]]
    assert grouped.other == [resources[2]]


def test_composition_after_sorting():
    """Sorting before grouping must not change membership, only order
    within each group."""
    resources = [
        _view("z-setup.exe", confidence="high", score=10),
        _view("a-game.zip", confidence="high", score=90),
        _view("m-readme.txt", confidence="high", score=50),
    ]
    sorted_view = sort_resources(resources, SortSpec(SortMode.RECOMMENDED))
    grouped = group_resources(sorted_view)

    assert [r.file.name for r in grouped.main] == ["a-game.zip", "z-setup.exe"]
    assert [r.file.name for r in grouped.other] == ["m-readme.txt"]


def test_composition_after_categorization_is_consistent():
    from app.services.categorization import categorize_resource

    v = _view("setup.exe", confidence="high")
    grouped = group_resources([v])
    assert categorize_resource(v) is ResourceCategory.INSTALLER
    assert grouped.main == [v]


def test_selection_unaffected_by_grouping():
    resources = [
        _view("setup.exe", confidence="high"),
        _view("readme.txt", confidence="high"),
        _view("patch.exe", confidence="high"),
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.toggle(resources[0])
    ctrl.toggle(resources[2])
    selected_before = ctrl.state.count

    _ = group_resources(resources)

    assert ctrl.state.count == selected_before
    assert ctrl.is_selected(resources[0])
    assert ctrl.is_selected(resources[2])
    assert not ctrl.is_selected(resources[1])


def test_total_helper():
    grouped = GroupedResources(main=[_view("a.exe", confidence="high")], optional=[], other=[])
    assert grouped.total() == 1


def test_by_group_returns_all_three_keys():
    grouped = group_resources([])
    keys = set(grouped.by_group().keys())
    assert keys == {ResourceGroup.MAIN, ResourceGroup.OPTIONAL, ResourceGroup.OTHER}


def test_full_pipeline_filter_sort_group():
    resources = [
        _view("setup.exe", confidence="high", score=90),
        _view("game.zip", confidence="high", score=80),
        _view("readme.txt", confidence="medium", score=10),
        _view("game.part1.rar", confidence="high", score=80),
        _view("patch.exe", confidence="high", score=70),
        _view("payload.iso", confidence="low", score=10),
    ]
    filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.SCORE_DESC))
    grouped = group_resources(sorted_view)

    assert {r.file.name for r in grouped.main} == {"setup.exe", "game.zip", "game.part1.rar"}
    assert grouped.optional == [resources[4]]
    assert grouped.other == []
