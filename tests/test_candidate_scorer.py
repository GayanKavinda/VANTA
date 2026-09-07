from app.core.models import ConfidenceLevel, ResourceProbeResult, ResolvedResource
from app.sources.candidate_scorer import (
    SCORE_CONTENT_DISPOSITION,
    SCORE_DOWNLOADABLE_MIME,
    SCORE_FILE_EXTENSION,
    classify,
    filter_accepted,
    score_candidate,
    to_resolved_resource,
)


def _ok_probe(*, filename=None, content_type="application/zip", size=100, supports_range=True, is_downloadable=True, final_url=None, status=200):
    return ResourceProbeResult(
        url="https://example.com/x",
        final_url=final_url or "https://example.com/x",
        status_code=status,
        content_type=content_type,
        size=size,
        filename=filename,
        supports_range=supports_range,
        is_downloadable=is_downloadable,
    )


def test_score_extension_only_is_high():
    score, reasons = score_candidate(url="https://example.com/game.zip")
    assert score >= 90
    assert any("extension" in r for r in reasons)


def test_score_zip_with_disposition_is_medium_or_high():
    score, _ = score_candidate(
        url="https://example.com/dl?id=1",
        probe=_ok_probe(filename="game.zip"),
    )
    assert score >= 60


def test_score_html_response_is_low():
    score, reasons = score_candidate(
        url="https://example.com/page",
        probe=_ok_probe(
            content_type="text/html",
            filename="none",
            is_downloadable=False,
        ),
    )
    assert score < 30
    assert any("HTML" in r or "web-document" in r for r in reasons)


def test_score_navigation_url_without_probe_is_low():
    score, _ = score_candidate(url="https://example.com/about-us")
    assert score < 30


def test_score_404_is_rejected():
    score, reasons = score_candidate(
        url="https://example.com/missing",
        probe=_ok_probe(status=404, is_downloadable=False),
    )
    assert score == 0
    assert any("404" in r for r in reasons)


def test_score_anchor_text_with_download_keyword_adds_score():
    score_with, _ = score_candidate(
        url="https://example.com/get?id=1",
        anchor_text="Download Now",
    )
    score_without, _ = score_candidate(
        url="https://example.com/get?id=1",
        anchor_text="Click here",
    )
    assert score_with > score_without


def test_classify_thresholds():
    assert classify(95) == ConfidenceLevel.HIGH
    assert classify(90) == ConfidenceLevel.HIGH
    assert classify(80) == ConfidenceLevel.MEDIUM
    assert classify(60) == ConfidenceLevel.MEDIUM
    assert classify(40) == ConfidenceLevel.LOW
    assert classify(29) == ConfidenceLevel.REJECTED
    assert classify(0) == ConfidenceLevel.REJECTED


def test_to_resolved_resource_uses_probe_metadata():
    resource = to_resolved_resource(
        source_url="https://example.com/dl?id=1",
        probe=_ok_probe(
            filename="game.zip",
            content_type="application/zip",
            size=2048,
            supports_range=True,
            final_url="https://cdn.example.com/game.zip",
        ),
    )
    assert resource.final_url == "https://cdn.example.com/game.zip"
    assert resource.filename == "game.zip"
    assert resource.size == 2048
    assert resource.content_type == "application/zip"
    assert resource.supports_range is True


def test_to_resolved_resource_filename_falls_back_to_url_path_segment():
    resource = to_resolved_resource(
        source_url="https://example.com/dl?id=1",
        probe=_ok_probe(filename=None, final_url="https://example.com/dl"),
    )
    assert resource.filename == "dl"


def test_to_resolved_resource_filename_falls_back_to_url_when_path_has_filename():
    resource = to_resolved_resource(
        source_url="https://example.com/dl?id=1",
        probe=_ok_probe(filename=None, final_url="https://example.com/dl/archive.zip"),
    )
    assert resource.filename == "archive.zip"


def test_filter_accepted_drops_rejected():
    accepted = [
        ResolvedResource(
            source_url="a", final_url="a", filename=None, size=None,
            content_type=None, supports_range=None,
            score=50, confidence=ConfidenceLevel.LOW,
        ),
        ResolvedResource(
            source_url="b", final_url="b", filename=None, size=None,
            content_type=None, supports_range=None,
            score=10, confidence=ConfidenceLevel.REJECTED,
        ),
        ResolvedResource(
            source_url="c", final_url="c", filename=None, size=None,
            content_type=None, supports_range=None,
            score=95, confidence=ConfidenceLevel.HIGH,
        ),
    ]
    result = filter_accepted(accepted)
    assert len(result) == 2
    assert {r.source_url for r in result} == {"a", "c"}


def test_score_constants_match_spec():
    assert SCORE_FILE_EXTENSION == 90
    assert SCORE_CONTENT_DISPOSITION == 35
    assert SCORE_DOWNLOADABLE_MIME == 25