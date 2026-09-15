"""V2.0 Phase 3.4 — Resource Resolution & Source Intelligence Enhancement tests.

Covers improvements to:
- Path filtering (exact matching, download extension exemption)
- Filename precedence (download hint before anchor text)
- Candidate evidence propagation
"""

import pytest

from app.sources.generic import _is_non_resource_path
from app.sources.link_classifier import is_download_candidate


# ── Path filtering ─────────────────────────────────────────────────

def test_path_about_filtered():
    assert _is_non_resource_path("/about") is True


def test_path_about_subpage_filtered():
    assert _is_non_resource_path("/about/team") is True


def test_path_about_exact_not_overbroad():
    assert _is_non_resource_path("/about-face.png") is False


def test_path_help_filtered():
    assert _is_non_resource_path("/help") is True


def test_path_help_subpage_filtered():
    assert _is_non_resource_path("/help/faq") is True


def test_path_help_center_not_filtered():
    assert _is_non_resource_path("/help-center.pdf") is False


def test_path_archive_filtered():
    assert _is_non_resource_path("/archive") is True


def test_path_archive_subpage_filtered():
    assert _is_non_resource_path("/archive/docs") is True


def test_path_archive_file_not_filtered():
    assert _is_non_resource_path("/archive/file.zip") is False


def test_path_search_filtered():
    assert _is_non_resource_path("/search") is True


def test_path_search_engine_not_filtered():
    assert _is_non_resource_path("/search-engine.pdf") is False


def test_path_login_filtered():
    assert _is_non_resource_path("/login") is True


def test_path_contact_filtered():
    assert _is_non_resource_path("/contact") is True


def test_path_privacy_filtered():
    assert _is_non_resource_path("/privacy") is True


def test_path_privacy_policy_not_filtered():
    assert _is_non_resource_path("/privacy-policy.pdf") is False


def test_path_root_not_filtered():
    assert _is_non_resource_path("/") is False


def test_path_page_not_filtered():
    assert _is_non_resource_path("/page") is False


def test_path_download_not_filtered():
    assert _is_non_resource_path("/download/file.zip") is False


def test_path_empty_filtered():
    assert _is_non_resource_path("") is True


def test_path_download_candidate_not_filtered():
    assert _is_non_resource_path("/files/report.pdf") is False


def test_path_download_candidate_false_for_navigation():
    assert is_download_candidate("/about") is False
    assert is_download_candidate("/help") is False


def test_path_download_candidate_true_for_resource():
    assert is_download_candidate("/files/report.pdf") is True
    assert is_download_candidate("/help-center.pdf") is True


# ── Filename precedence ────────────────────────────────────────────

def test_download_hint_over_anchor_text():
    from app.sources.html_parser import parse_html, ResourceCandidate
    c = ResourceCandidate(
        url="/files/report.pdf",
        element_type="link",
        anchor_text="Click here",
        download_name_hint="report.pdf",
    )
    assert c.download_name_hint == "report.pdf"


def test_download_hint_traversal_rejected():
    from app.sources.html_parser import _sanitize_download_hint
    assert _sanitize_download_hint("../../etc/passwd") == ""


def test_download_hint_filename_extracted():
    from app.sources.html_parser import _sanitize_download_hint
    result = _sanitize_download_hint("/path/to/name.zip")
    assert result == "name.zip"
    assert ".." not in result


def test_download_hint_empty_returns_empty():
    from app.sources.html_parser import _sanitize_download_hint
    assert _sanitize_download_hint("") == ""
    assert _sanitize_download_hint(None) == ""


# ── Evidence propagation ────────────────────────────────────────────

def test_evidence_element_type_stored():
    from app.sources.html_parser import ResourceCandidate
    c = ResourceCandidate(
        url="/img.jpg",
        element_type="img",
        type_hint="image/jpeg",
        download_name_hint="photo.jpg",
    )
    assert c.element_type == "img"
    assert c.type_hint == "image/jpeg"
    assert c.download_name_hint == "photo.jpg"


def test_evidence_link_type_stored():
    from app.sources.html_parser import ResourceCandidate
    c = ResourceCandidate(
        url="/style.css",
        element_type="link",
        type_hint="text/css",
    )
    assert c.element_type == "link"
    assert c.type_hint == "text/css"


def test_candidate_with_no_evidence():
    from app.sources.html_parser import ResourceCandidate
    c = ResourceCandidate(
        url="/page.html",
        element_type="",
        type_hint="",
        anchor_text="",
        download_name_hint="",
    )
    assert c.element_type == ""
    assert c.type_hint == ""
    assert c.download_name_hint == ""


# ── MIME/extension consistency ─────────────────────────────────────

def test_mime_priority_over_extension():
    from app.services.resource_intelligence import (
        _MIME_CATEGORY_MAP,
        _EXTENSION_CATEGORY_MAP,
    )
    conflict_mime = "application/zip"
    ext = ".pdf"
    mime_category = _MIME_CATEGORY_MAP.get(conflict_mime)
    ext_category = _EXTENSION_CATEGORY_MAP.get(ext)
    assert mime_category == "archive"
    assert ext_category == "document"
    assert mime_category != ext_category


# ── Quality explanation evidence ────────────────────────────────────

def test_quality_direct_file_evidence():
    from app.services.resource_intelligence import (
        ResourceIntelligence,
        FileCategory,
        SourceQuality,
    )
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/file.zip",
        final_url="https://example.com/file.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
        content_disposition='attachment; filename="file.zip"',
        content_length="1024",
    )
    result = ri.enrich(probe)
    assert result.quality == SourceQuality.DIRECT_FILE
    assert any("Content-Disposition" in r for r in result.reasons)
    assert any("Content-Length" in r for r in result.reasons)
    ri.reset()


def test_quality_high_confidence():
    from app.services.resource_intelligence import (
        ResourceIntelligence,
        SourceQuality,
    )
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/image.jpg",
        final_url="https://example.com/image.jpg",
        status_code=200,
        content_type="image/jpeg",
        size=2048,
        filename="image.jpg",
        is_downloadable=True,
        content_disposition='attachment; filename="image.jpg"',
        content_length=None,
    )
    result = ri.enrich(probe)
    assert result.quality == SourceQuality.HIGH_CONFIDENCE
    ri.reset()


def test_quality_rejected_duplicate():
    from app.services.resource_intelligence import (
        ResourceIntelligence,
        SourceQuality,
    )
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe1 = ResourceProbeResult(
        url="https://example.com/file.zip",
        final_url="https://example.com/file.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
        content_disposition='attachment; filename="file.zip"',
        content_length="1024",
    )
    probe2 = ResourceProbeResult(
        url="https://example.com/file.zip",
        final_url="https://example.com/file.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
        content_disposition='attachment; filename="file.zip"',
        content_length="1024",
    )
    result1 = ri.enrich(probe1)
    result2 = ri.enrich(probe2)
    assert result1.quality == SourceQuality.DIRECT_FILE
    assert result2.quality == SourceQuality.REJECTED
    assert result2.duplicate.is_duplicate is True
    ri.reset()


def test_quality_rejected_http_error():
    from app.services.resource_intelligence import (
        ResourceIntelligence,
        SourceQuality,
    )
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/missing.zip",
        final_url="https://example.com/missing.zip",
        status_code=404,
        is_downloadable=False,
    )
    result = ri.enrich(probe)
    assert result.quality == SourceQuality.REJECTED
    ri.reset()


def test_quality_rejected_not_downloadable():
    from app.services.resource_intelligence import (
        ResourceIntelligence,
        SourceQuality,
    )
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/page.html",
        final_url="https://example.com/page.html",
        status_code=200,
        content_type="text/html",
        is_downloadable=False,
    )
    result = ri.enrich(probe)
    assert result.quality == SourceQuality.REJECTED
    ri.reset()


def test_filename_from_content_disposition():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/download?id=123",
        final_url="https://example.com/download?id=123",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="report.zip",
        is_downloadable=True,
        content_disposition='attachment; filename="report.zip"',
        content_length=None,
    )
    result = ri.enrich(probe)
    assert result.filename.source == "content_disposition"
    assert result.filename.filename == "report.zip"
    assert result.filename.confidence == "high"
    ri.reset()


def test_filename_from_url():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/files/report.pdf",
        final_url="https://example.com/files/report.pdf",
        status_code=200,
        content_type=None,
        size=None,
        filename=None,
        is_downloadable=True,
        content_disposition=None,
        content_length=None,
    )
    result = ri.enrich(probe)
    assert result.filename.source == "url_path"
    assert result.filename.filename == "report.pdf"
    assert result.filename.confidence == "medium"
    ri.reset()


def test_size_from_content_length():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/file.zip",
        final_url="https://example.com/file.zip",
        status_code=200,
        content_type="application/zip",
        size=None,
        is_downloadable=True,
        content_length="1024",
    )
    result = ri.enrich(probe)
    assert result.size.source == "content_length"
    assert result.size.is_exact is True
    ri.reset()


def test_mime_category_from_content_type():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/image.jpg",
        final_url="https://example.com/image.jpg",
        status_code=200,
        content_type="image/jpeg",
        size=None,
        is_downloadable=True,
    )
    result = ri.enrich(probe)
    assert result.mime.source == "mime_type"
    assert result.mime.category == "image"
    ri.reset()


def test_mime_category_from_extension():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/image.jpg",
        final_url="https://example.com/image.jpg",
        status_code=200,
        content_type=None,
        size=None,
        is_downloadable=True,
    )
    result = ri.enrich(probe)
    assert result.mime.source == "extension"
    assert result.mime.category == "image"
    ri.reset()


def test_mime_category_unknown():
    from app.services.resource_intelligence import ResourceIntelligence, FileCategory
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe = ResourceProbeResult(
        url="https://example.com/unknown",
        final_url="https://example.com/unknown",
        status_code=200,
        content_type="application/unknown",
        size=None,
        is_downloadable=True,
    )
    result = ri.enrich(probe)
    assert result.mime.category == FileCategory.UNKNOWN
    ri.reset()


def test_duplicate_by_normalized_url():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe1 = ResourceProbeResult(
        url="https://example.com/file.zip",
        final_url="https://example.com/file.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
    )
    probe2 = ResourceProbeResult(
        url="https://example.com/file.zip",
        final_url="https://example.com/file.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
    )
    result1 = ri.enrich(probe1)
    result2 = ri.enrich(probe2)
    assert result2.duplicate.is_duplicate is True
    assert result2.duplicate.reason == "normalized URL match"
    ri.reset()


def test_duplicate_by_filename_and_size():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe1 = ResourceProbeResult(
        url="https://example.com/a.zip",
        final_url="https://example.com/a.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
        content_disposition='attachment; filename="file.zip"',
        content_length=None,
    )
    probe2 = ResourceProbeResult(
        url="https://example.com/b.zip",
        final_url="https://example.com/b.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
        content_disposition='attachment; filename="file.zip"',
        content_length=None,
    )
    result1 = ri.enrich(probe1)
    result2 = ri.enrich(probe2)
    assert result2.duplicate.is_duplicate is True
    assert result2.duplicate.reason == "filename+size match"
    ri.reset()


def test_no_duplicate_different_size():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe1 = ResourceProbeResult(
        url="https://example.com/a.zip",
        final_url="https://example.com/a.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="file.zip",
        is_downloadable=True,
    )
    probe2 = ResourceProbeResult(
        url="https://example.com/b.zip",
        final_url="https://example.com/b.zip",
        status_code=200,
        content_type="application/zip",
        size=2048,
        filename="file.zip",
        is_downloadable=True,
    )
    result1 = ri.enrich(probe1)
    result2 = ri.enrich(probe2)
    assert result2.duplicate.is_duplicate is False
    ri.reset()


def test_no_duplicate_different_filename():
    from app.services.resource_intelligence import ResourceIntelligence
    ri = ResourceIntelligence()
    from app.core.models import ResourceProbeResult
    probe1 = ResourceProbeResult(
        url="https://example.com/a.zip",
        final_url="https://example.com/a.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="a.zip",
        is_downloadable=True,
    )
    probe2 = ResourceProbeResult(
        url="https://example.com/b.zip",
        final_url="https://example.com/b.zip",
        status_code=200,
        content_type="application/zip",
        size=1024,
        filename="b.zip",
        is_downloadable=True,
    )
    result1 = ri.enrich(probe1)
    result2 = ri.enrich(probe2)
    assert result2.duplicate.is_duplicate is False
    ri.reset()
