"""V1.9.0 Phase 1 — Resource filtering tests.

Validates the pure filtering model. The contract under test:

  * pure Python (no Qt, no I/O)
  * does not mutate the input list
  * preserves input ordering in the output
  * filtering is independent of selection
  * typed enum (`FilterMode`) + `ResourceFilter` dataclass
  * FILE_TYPE matching is case-insensitive, checks URL / filename / MIME
  * unknown / invalid filter raises (no silent fallback)
  * safe for resources missing filename or content_type
"""
from __future__ import annotations

import pytest

from app.core.models import ConfidenceLevel, DownloadFile
from app.services.analysis_view import ResourceView
from app.services.filtering import (
    FilterMode,
    ResourceFilter,
    apply_filter,
)


def _view(
    name: str,
    *,
    confidence: str = "low",
    content_type: str | None = None,
    url: str | None = None,
    score: int = 0,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=url or f"https://example.com/{name}",
            content_type=content_type,
        ),
        confidence=confidence,
        score=score,
    )


@pytest.fixture
def sample() -> list[ResourceView]:
    return [
        _view("setup.exe", confidence=ConfidenceLevel.HIGH, content_type="application/octet-stream", score=95),
        _view("manual.pdf", confidence=ConfidenceLevel.MEDIUM, content_type="application/pdf", score=70),
        _view("game.zip", confidence=ConfidenceLevel.HIGH, content_type="application/zip", score=90),
        _view("readme.txt", confidence=ConfidenceLevel.LOW, content_type="text/plain", score=10),
        _view("patch.zip", confidence=ConfidenceLevel.MEDIUM, content_type="application/zip", score=60),
    ]


def test_all_returns_every_resource(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.ALL))
    assert out == sample


def test_high_returns_only_high_confidence(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.HIGH))
    assert [v.file.name for v in out] == ["setup.exe", "game.zip"]


def test_medium_returns_only_medium_confidence(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.MEDIUM))
    assert [v.file.name for v in out] == ["manual.pdf", "patch.zip"]


def test_low_returns_only_low_confidence(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.LOW))
    assert [v.file.name for v in out] == ["readme.txt"]


def test_filter_preserves_source_order(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.MEDIUM))
    assert [v.file.name for v in out] == ["manual.pdf", "patch.zip"]


def test_filter_does_not_mutate_input(sample):
    snapshot = [v.file.name for v in sample]
    _ = apply_filter(sample, ResourceFilter(FilterMode.HIGH))
    assert [v.file.name for v in sample] == snapshot


def test_empty_resources_returns_empty():
    out = apply_filter([], ResourceFilter(FilterMode.ALL))
    assert out == []


def test_file_type_matches_extension(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.FILE_TYPE, file_type="zip"))
    assert [v.file.name for v in out] == ["game.zip", "patch.zip"]


def test_file_type_matching_is_case_insensitive(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.FILE_TYPE, file_type="ZIP"))
    assert [v.file.name for v in out] == ["game.zip", "patch.zip"]


def test_file_type_matches_content_type_subtype(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.FILE_TYPE, file_type="pdf"))
    assert [v.file.name for v in out] == ["manual.pdf"]


def test_unknown_file_type_returns_empty(sample):
    out = apply_filter(sample, ResourceFilter(FilterMode.FILE_TYPE, file_type="rar"))
    assert out == []


def test_combined_filter_high_then_zip_is_intersection(sample):
    out = apply_filter(
        sample,
        ResourceFilter(FilterMode.FILE_TYPE, file_type="zip"),
    )
    out_high = apply_filter(out, ResourceFilter(FilterMode.HIGH))
    assert [v.file.name for v in out_high] == ["game.zip"]


def test_unknown_confidence_is_not_accidentally_high(sample):
    """A resource with an unrecognised confidence string must not be
    matched by HIGH/MEDIUM/LOW filters."""
    weird = _view("weird.bin", confidence="uncertain", content_type="application/octet-stream")
    out = apply_filter([weird], ResourceFilter(FilterMode.HIGH))
    assert out == []
    out = apply_filter([weird], ResourceFilter(FilterMode.MEDIUM))
    assert out == []
    out = apply_filter([weird], ResourceFilter(FilterMode.LOW))
    assert out == []


def test_resources_without_filename_are_safe():
    v = _view("", confidence=ConfidenceLevel.HIGH, url="https://example.com/", content_type="application/zip")
    out = apply_filter([v], ResourceFilter(FilterMode.FILE_TYPE, file_type="zip"))
    assert out == [v]
    out = apply_filter([v], ResourceFilter(FilterMode.FILE_TYPE, file_type="exe"))
    assert out == []


def test_resources_without_content_type_are_safe():
    v = _view("notes.txt", confidence=ConfidenceLevel.HIGH, content_type=None)
    out = apply_filter([v], ResourceFilter(FilterMode.FILE_TYPE, file_type="zip"))
    assert out == []


def test_filter_mode_must_be_enum():
    with pytest.raises(TypeError):
        apply_filter([], "all")  # type: ignore[arg-type]


def test_file_type_without_mode_is_rejected():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.HIGH, file_type="zip")


def test_file_type_mode_requires_file_type():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.FILE_TYPE)


def test_file_type_with_whitespace_is_rejected():
    with pytest.raises(ValueError):
        ResourceFilter(FilterMode.FILE_TYPE, file_type="zip rar")


def test_file_type_normalization_strips_leading_dot_and_case():
    f = ResourceFilter(FilterMode.FILE_TYPE, file_type=".ZIP")
    assert f.file_type == "zip"


def test_rejected_confidence_is_excluded_from_low():
    rejected = _view("ignored.html", confidence=ConfidenceLevel.REJECTED, content_type="text/html")
    out = apply_filter([rejected], ResourceFilter(FilterMode.LOW))
    assert out == []


def test_file_type_matches_url_extension_when_no_filename():
    v = ResourceView(
        file=DownloadFile(
            name="",
            url="https://example.com/downloads/build.iso",
            content_type="application/octet-stream",
        ),
        confidence=ConfidenceLevel.HIGH,
    )
    out = apply_filter([v], ResourceFilter(FilterMode.FILE_TYPE, file_type="iso"))
    assert out == [v]
