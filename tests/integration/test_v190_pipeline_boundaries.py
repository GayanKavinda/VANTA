from __future__ import annotations

import pytest

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import ResourceView
from app.services.categorization import categorize_resource
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
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
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


@pytest.mark.integration
def test_boundary_empty_pipeline():
    resources = []
    filtered = apply_filter(resources, ResourceFilter(FilterMode.ALL))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
    grouped = group_resources(sorted_view)

    assert filtered == []
    assert sorted_view == []
    assert grouped.total() == 0


@pytest.mark.integration
def test_boundary_single_resource():
    v = make_view("only.exe", ConfidenceLevel.HIGH, 90)
    filtered = apply_filter([v], ResourceFilter(FilterMode.ALL))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
    grouped = group_resources(sorted_view)

    assert grouped.main == [v]
    assert grouped.total() == 1


@pytest.mark.integration
def test_boundary_missing_filename():
    v = ResourceView(
        file=DownloadFile(name="", url="https://example.com/", size=None),
        confidence=ConfidenceLevel.HIGH,
        score=80,
    )
    filtered = apply_filter([v], ResourceFilter(FilterMode.ALL))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
    grouped = group_resources(sorted_view)

    assert grouped.total() == 1


@pytest.mark.integration
def test_boundary_missing_size():
    v = make_view("a.zip", size=None)
    filtered = apply_filter([v], ResourceFilter(FilterMode.ALL))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.SIZE_ASC))
    grouped = group_resources(sorted_view)

    assert grouped.total() == 1


@pytest.mark.integration
def test_boundary_missing_content_type():
    v = make_view("payload", content_type=None)
    grouped = group_resources([v])
    assert grouped.total() == 1


@pytest.mark.integration
def test_boundary_rejected_confidence():
    v = make_view("setup.exe", ConfidenceLevel.REJECTED, 0)
    grouped = group_resources([v])
    assert grouped.other == [v]


@pytest.mark.integration
def test_boundary_duplicate_references_preserved():
    v = make_view("dup.zip", ConfidenceLevel.HIGH, 90)
    grouped = group_resources([v, v, v])
    assert grouped.total() == 3
    assert grouped.main == [v, v, v]


@pytest.mark.integration
def test_boundary_unusual_mime_types():
    """Unusual MIME types must not crash categorization."""
    views = [
        make_view("a.bin", content_type="application/x-unknown"),
        make_view("b.dat", content_type="application/octet-stream"),
        make_view("c", content_type=""),
    ]
    for v in views:
        assert categorize_resource(v) is not None
    grouped = group_resources(views)
    assert grouped.total() == 3


@pytest.mark.integration
def test_stress_many_resources():
    resources = [
        make_view(
            f"file_{i}.zip",
            ConfidenceLevel.HIGH,
            50 + i,
            size=1000 + i,
            content_type="application/zip",
        )
        for i in range(500)
    ]
    filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.SCORE_DESC))
    grouped = group_resources(sorted_view)

    assert len(filtered) == 500
    assert len(sorted_view) == 500
    assert grouped.total() == 500
    assert [r.score for r in sorted_view] == sorted(
        [r.score for r in resources], reverse=True
    )


@pytest.mark.integration
def test_stress_repeated_filter_sort_group():
    resources = [
        make_view(f"f{i}.zip", ConfidenceLevel.HIGH, 50 + i)
        for i in range(100)
    ]
    for _ in range(10):
        filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
        sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
        grouped = group_resources(sorted_view)
        assert grouped.total() == 100


@pytest.mark.integration
def test_stress_selection_survives_repeated_pipeline():
    resources = [
        make_view(f"f{i}.zip", ConfidenceLevel.HIGH, 50 + i)
        for i in range(100)
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])
    ctrl.select_all()
    assert ctrl.state.count == 100

    for mode in (FilterMode.HIGH, FilterMode.ALL):
        filtered = apply_filter(resources, ResourceFilter(mode))
        sorted_view = sort_resources(filtered, SortSpec(SortMode.SCORE_DESC))
        group_resources(sorted_view)

    assert ctrl.state.count == 100


@pytest.mark.integration
def test_stress_filter_then_select_visible_preserves_hidden():
    resources = [
        make_view(f"f{i}.zip", ConfidenceLevel.HIGH, 50 + i)
        for i in range(100)
    ]
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([resource_id_for(r) for r in resources])

    high = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    ctrl.select_visible([resource_id_for(r) for r in high])

    assert ctrl.state.count == 100

    medium = apply_filter(resources, ResourceFilter(FilterMode.MEDIUM))
    ctrl.select_visible([resource_id_for(r) for r in medium])

    assert ctrl.state.count == 100


@pytest.mark.integration
def test_stress_mixed_confidence_pipeline():
    """Resources with mixed confidence must all be handled."""
    resources = []
    for i in range(50):
        resources.append(make_view(f"high_{i}.exe", ConfidenceLevel.HIGH, 90 + i))
    for i in range(50):
        resources.append(make_view(f"low_{i}.exe", ConfidenceLevel.LOW, 10 + i))
    for i in range(50):
        resources.append(make_view(f"medium_{i}.exe", ConfidenceLevel.MEDIUM, 50 + i))

    filtered = apply_filter(resources, ResourceFilter(FilterMode.ALL))
    assert len(filtered) == 150

    sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
    grouped = group_resources(sorted_view)

    assert grouped.total() == 150
    assert len(grouped.main) == 100
    assert len(grouped.optional) == 50
    assert len(grouped.other) == 0


@pytest.mark.integration
def test_stress_no_resources_lost_through_pipeline():
    resources = [
        make_view(f"f{i}.zip", ConfidenceLevel.HIGH, 50 + i)
        for i in range(200)
    ]
    filtered = apply_filter(resources, ResourceFilter(FilterMode.HIGH))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.SCORE_DESC))
    grouped = group_resources(sorted_view)

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

    assert len(seen) == 200