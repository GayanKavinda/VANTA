"""Confidence scoring for candidate resources.

Produces a 0..100 score and an explainable list of reasons.

The numbers below are starting weights and are deliberately conservative.
They are tuned via tests and will evolve in later revisions.
"""
from typing import Iterable, Optional

from app.core.models import ConfidenceLevel, ResourceProbeResult, ResolvedResource
from app.sources.http_headers import (
    extract_filename_from_url,
    has_download_extension,
    normalize_media_type,
)


SCORE_FILE_EXTENSION = 90
SCORE_CONTENT_DISPOSITION = 35
SCORE_DOWNLOADABLE_MIME = 25
SCORE_FILENAME_AVAILABLE = 5
SCORE_DOWNLOAD_LIKE_PATH = 10
SCORE_ANCHOR_TEXT = 5

SCORE_HTML_RESPONSE = -50
SCORE_NAVIGATION_URL = -40

HIGH_THRESHOLD = 90
MEDIUM_THRESHOLD = 60
LOW_THRESHOLD = 30


_DOWNLOAD_LIKE_PATH_TOKENS = (
    "/download",
    "/file",
    "/attachment",
    "/get",
)


def _path_has_download_hint(path: str) -> bool:
    p = (path or "").lower()
    return any(tok in p for tok in _DOWNLOAD_LIKE_PATH_TOKENS)


def score_candidate(
    *,
    url: str,
    anchor_text: str = "",
    probe: Optional[ResourceProbeResult] = None,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if probe and probe.status_code >= 400:
        score += SCORE_HTML_RESPONSE
        reasons.append(f"HTTP {probe.status_code} (non-success)")
        return max(score, 0), reasons

    if probe and probe.is_downloadable:
        media = normalize_media_type(probe.content_type)
        if media:
            score += SCORE_DOWNLOADABLE_MIME
            reasons.append(f"downloadable MIME: {media}")
        if probe.filename:
            score += SCORE_CONTENT_DISPOSITION
            reasons.append("Content-Disposition filename")
    elif probe and not probe.is_downloadable and probe.status_code == 200:
        media = normalize_media_type(probe.content_type)
        if media and ("html" in media or "xml" in media or "json" in media):
            score += SCORE_HTML_RESPONSE
            reasons.append(f"web-document MIME: {media}")

    if has_download_extension(url):
        score += SCORE_FILE_EXTENSION
        reasons.append("file extension")

    if probe and probe.filename:
        pass
    elif not probe and not has_download_extension(url):
        url_filename = extract_filename_from_url(url)
        if url_filename:
            score += SCORE_FILENAME_AVAILABLE
            reasons.append("filename from URL path")

    from urllib.parse import urlsplit
    path = urlsplit(url).path if url else ""
    if _path_has_download_hint(path):
        score += SCORE_DOWNLOAD_LIKE_PATH
        reasons.append("download-like path")

    if anchor_text and anchor_text.strip():
        text = anchor_text.strip().lower()
        if any(kw in text for kw in ("download", "get file", "install")):
            score += SCORE_ANCHOR_TEXT
            reasons.append("download-suggesting anchor text")

    if not probe and not has_download_extension(url) and not _path_has_download_hint(path):
        score += SCORE_NAVIGATION_URL
        reasons.append("navigation-style URL")

    if score < 0:
        score = 0

    return score, reasons


def classify(score: int) -> str:
    if score >= HIGH_THRESHOLD:
        return ConfidenceLevel.HIGH
    if score >= MEDIUM_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    if score >= LOW_THRESHOLD:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.REJECTED


def to_resolved_resource(
    *,
    source_url: str,
    probe: Optional[ResourceProbeResult],
    anchor_text: str = "",
    filename_override: Optional[str] = None,
) -> ResolvedResource:
    score, reasons = score_candidate(
        url=source_url,
        anchor_text=anchor_text,
        probe=probe,
    )
    confidence = classify(score)

    final_url = (probe.final_url if probe else source_url) or source_url
    filename = (
        filename_override
        or (probe.filename if probe else None)
        or extract_filename_from_url(final_url)
    )
    size = probe.size if probe else None
    content_type = probe.content_type if probe else None
    supports_range = probe.supports_range if probe else None

    return ResolvedResource(
        source_url=source_url,
        final_url=final_url,
        filename=filename,
        size=size,
        content_type=content_type,
        supports_range=supports_range,
        score=score,
        confidence=confidence,
        reasons=reasons,
    )


def filter_accepted(
    resources: Iterable[ResolvedResource],
) -> list[ResolvedResource]:
    return [r for r in resources if r.confidence != ConfidenceLevel.REJECTED]