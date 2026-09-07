from __future__ import annotations

import pytest

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import ResourceView
from app.services.categorization import (
    ResourceCategory,
    categorize_resource,
)
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
    *,
    confidence: str = ConfidenceLevel.HIGH,
    score: int = 80,
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


@pytest.mark.integration
def test_v190_filter_sort_categorize_group_pipeline():
    """Validate the complete V1.9 resource intelligence pipeline.

    Analyzer output is represented by ResourceView instances here.
    The purpose is to verify that all V1.9 services compose correctly.
    """
    resources = [
        make_view("setup.exe", score=95, content_type="application/octet-stream"),
        make_view("game.zip", score=90, content_type="application/zip"),
        make_view("readme.txt", score=40, content_type="text/plain"),
        make_view("patch.exe", score=80, content_type="application/octet-stream"),
    ]

    filtered = apply_filter(
        resources,
        ResourceFilter(FilterMode.HIGH),
    )
    assert len(filtered) == 4

    sorted_resources = sort_resources(
        filtered,
        SortSpec(SortMode.SCORE_DESC),
    )
    assert [r.score for r in sorted_resources] == [95, 90, 80, 40]

    categories = [categorize_resource(r) for r in sorted_resources]
    assert categories == [
        ResourceCategory.INSTALLER,
        ResourceCategory.ARCHIVE,
        ResourceCategory.PATCH,
        ResourceCategory.DOCUMENTATION,
    ]

    grouped = group_resources(sorted_resources)
    assert [r.file.name for r in grouped.main] == ["setup.exe", "game.zip"]
    assert [r.file.name for r in grouped.optional] == ["patch.exe"]
    assert [r.file.name for r in grouped.other] == ["readme.txt"]
    assert grouped.total() == len(sorted_resources)


@pytest.mark.integration
def test_v190_http_fixture_smoke_test(integration_server):
    """HTTP fixture smoke test — guards the integration test server only.

    The V1.6+ security layer correctly rejects loopback hosts (127.0.0.1),
    so the real analyzer download pipeline cannot be exercised end-to-end
    against a local test server. Instead, the analyzer pipeline is tested
    against synthesized `ResourceView` instances in the boundary and
    selection tests (see `test_v190_pipeline_boundaries.py` and
    `test_v190_pipeline_selection.py`).

    This test verifies that the fixture itself is functional: the server
    starts, serves the expected HTML page, and exposes the expected
    resource links.
    """
    import urllib.request

    with urllib.request.urlopen(f"{integration_server}/page", timeout=5) as resp:
        body = resp.read().decode("utf-8")

    assert "VANTA Integration Test" in body
    assert "/game/setup.exe" in body
    assert "/game/game.zip" in body
    assert "/game/readme.txt" in body
