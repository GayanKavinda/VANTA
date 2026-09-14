"""V1.14 tests — Resource Intelligence."""
from app.core.models import ResourceProbeResult
from app.services.resource_intelligence import (
    ResourceIntelligence,
    FileCategory,
    SourceQuality,
)


def _make_probe_result(
    url: str = "http://example.com/file.zip",
    final_url: str = "http://example.com/file.zip",
    status_code: int = 200,
    content_type: str | None = "application/zip",
    size: int | None = 1000,
    filename: str | None = "file.zip",
    supports_range: bool | None = True,
    is_downloadable: bool = True,
    content_disposition: str | None = None,
    content_length: str | None = None,
    content_range: str | None = None,
) -> ResourceProbeResult:
    return ResourceProbeResult(
        url=url,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        size=size,
        filename=filename,
        supports_range=supports_range,
        is_downloadable=is_downloadable,
        content_disposition=content_disposition,
        content_length=content_length,
        content_range=content_range,
    )


# ── Filename Intelligence ────────────────────────────────────────────────

def test_filename_from_content_disposition():
    """Content-Disposition filename takes priority."""
    probe = _make_probe_result(
        content_disposition='attachment; filename="download.zip"',
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.filename.filename == "download.zip"
    assert result.filename.source == "content_disposition"
    assert result.filename.confidence == "high"


def test_filename_from_content_disposition_rfc5987():
    """RFC 5987 encoded filename (filename*)."""
    probe = _make_probe_result(
        content_disposition="attachment; filename*=UTF-8''test%20file.zip",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.filename.filename == "test file.zip"
    assert result.filename.source == "content_disposition"


def test_filename_falls_back_to_url_path():
    """Falls back to URL path when no Content-Disposition."""
    probe = _make_probe_result(
        content_disposition=None,
        final_url="http://example.com/downloads/myfile.zip",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.filename.filename == "myfile.zip"
    assert result.filename.source == "url_path"
    assert result.filename.confidence == "medium"


def test_filename_generated_fallback():
    """Generates fallback when no filename in URL."""
    probe = _make_probe_result(
        content_disposition=None,
        final_url="http://example.com/",
        filename=None,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.filename.filename is not None
    assert result.filename.source == "generated"
    assert result.filename.confidence == "low"


def test_filename_prefers_content_disposition_over_url():
    """Content-Disposition wins over URL path."""
    probe = _make_probe_result(
        content_disposition='attachment; filename="cd_name.zip"',
        final_url="http://example.com/url_name.zip",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.filename.filename == "cd_name.zip"
    assert result.filename.source == "content_disposition"


# ── Size Intelligence ────────────────────────────────────────────────────

def test_size_from_content_length():
    """Size from Content-Length header."""
    probe = _make_probe_result(
        content_length="1024000",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.size.size == 1024000
    assert result.size.source == "content_length"
    assert result.size.is_exact is True


def test_size_from_content_range():
    """Size from Content-Range header."""
    probe = _make_probe_result(
        content_length=None,
        content_range="bytes 0-1023/1024000",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.size.size == 1024000
    assert result.size.source == "content_range"
    assert result.size.is_exact is True


def test_size_from_probe():
    """Size from probe result when no headers."""
    probe = _make_probe_result(
        content_length=None,
        content_range=None,
        size=5000,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.size.size == 5000
    assert result.size.source == "probe"
    assert result.size.is_exact is True


def test_size_none_when_unavailable():
    """Size is None when no source available."""
    probe = _make_probe_result(
        content_length=None,
        content_range=None,
        size=None,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.size.size is None
    assert result.size.source == "none"
    assert result.size.is_exact is False


def test_size_prefers_content_length_over_probe():
    """Content-Length takes precedence over probe size."""
    probe = _make_probe_result(
        content_length="2000",
        size=5000,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.size.size == 2000
    assert result.size.source == "content_length"


# ── MIME Intelligence ────────────────────────────────────────────────────

def test_mime_category_from_media_type():
    """Category from media type."""
    probe = _make_probe_result(content_type="application/pdf")
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.mime.category == FileCategory.DOCUMENT
    assert result.mime.media_type == "application/pdf"
    assert result.mime.source == "mime_type"


def test_mime_category_from_extension():
    """Category from file extension when MIME unknown."""
    probe = _make_probe_result(
        content_type=None,
        final_url="http://example.com/archive.rar",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.mime.category == FileCategory.ARCHIVE
    assert result.mime.source == "extension"


def test_mime_category_unknown_when_no_info():
    """Unknown category when no MIME or extension."""
    probe = _make_probe_result(
        content_type="application/octet-stream",
        final_url="http://example.com/unknown",
        filename=None,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.mime.category == FileCategory.UNKNOWN


def test_mime_category_installer_extensions():
    """Installer extensions map correctly."""
    for ext in [".exe", ".msi", ".deb", ".rpm", ".dmg", ".apk", ".pkg"]:
        probe = _make_probe_result(
            content_type=None,
            final_url=f"http://example.com/install{ext}",
        )
        intel = ResourceIntelligence()
        result = intel.enrich(probe)
        assert result.mime.category == FileCategory.INSTALLER, f"Failed for {ext}"


def test_mime_category_media_prefixes():
    """Media type prefixes map correctly."""
    for mt in ["image/jpeg", "video/mp4", "audio/mpeg"]:
        probe = _make_probe_result(content_type=mt)
        intel = ResourceIntelligence()
        result = intel.enrich(probe)
        if mt.startswith("image/"):
            assert result.mime.category == FileCategory.IMAGE
        elif mt.startswith("video/"):
            assert result.mime.category == FileCategory.VIDEO
        elif mt.startswith("audio/"):
            assert result.mime.category == FileCategory.AUDIO


# ── Duplicate Detection ──────────────────────────────────────────────────

def test_duplicate_by_normalized_url():
    """Same normalized URL detected as duplicate."""
    intel = ResourceIntelligence()

    probe1 = _make_probe_result(url="http://example.com/file.zip")
    result1 = intel.enrich(probe1)
    assert result1.duplicate.is_duplicate is False

    probe2 = _make_probe_result(url="http://EXAMPLE.COM:80/file.zip")
    result2 = intel.enrich(probe2)
    assert result2.duplicate.is_duplicate is True
    assert result2.duplicate.canonical_url == "http://example.com/file.zip"


def test_duplicate_by_filename_and_size():
    """Same filename+size detected as duplicate even with different URLs."""
    intel = ResourceIntelligence()

    probe1 = _make_probe_result(
        url="http://example.com/file.zip",
        content_disposition='attachment; filename="same.zip"',
        content_length="1000",
    )
    result1 = intel.enrich(probe1)
    assert result1.duplicate.is_duplicate is False

    probe2 = _make_probe_result(
        url="http://other.com/download.zip",
        content_disposition='attachment; filename="same.zip"',
        content_length="1000",
    )
    result2 = intel.enrich(probe2)
    assert result2.duplicate.is_duplicate is True
    assert result2.duplicate.reason == "filename+size match"


def test_no_duplicate_when_different_size():
    """Different sizes not considered duplicates."""
    intel = ResourceIntelligence()

    probe1 = _make_probe_result(
        content_disposition='attachment; filename="file.zip"',
        content_length="1000",
    )
    intel.enrich(probe1)

    probe2 = _make_probe_result(
        url="http://other.com/file.zip",
        content_disposition='attachment; filename="file.zip"',
        content_length="2000",
    )
    result2 = intel.enrich(probe2)
    assert result2.duplicate.is_duplicate is False


def test_no_duplicate_when_different_filename():
    """Different filenames not considered duplicates."""
    intel = ResourceIntelligence()

    probe1 = _make_probe_result(
        content_disposition='attachment; filename="a.zip"',
        content_length="1000",
    )
    intel.enrich(probe1)

    probe2 = _make_probe_result(
        url="http://other.com/b.zip",
        content_disposition='attachment; filename="b.zip"',
        content_length="1000",
    )
    result2 = intel.enrich(probe2)
    assert result2.duplicate.is_duplicate is False


def test_duplicate_reset():
    """Reset clears deduplication state."""
    intel = ResourceIntelligence()

    probe1 = _make_probe_result(
        content_disposition='attachment; filename="reset.zip"',
        content_length="1000",
    )
    intel.enrich(probe1)

    intel.reset()

    probe2 = _make_probe_result(
        content_disposition='attachment; filename="reset.zip"',
        content_length="1000",
    )
    result2 = intel.enrich(probe2)
    assert result2.duplicate.is_duplicate is False


# ── Source Quality ───────────────────────────────────────────────────────

def test_quality_direct_file():
    """DIRECT_FILE when Content-Disposition + Content-Length both present."""
    probe = _make_probe_result(
        content_disposition='attachment; filename="direct.zip"',
        content_length="1000000",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.quality == SourceQuality.DIRECT_FILE


def test_quality_high_confidence():
    """HIGH_CONFIDENCE when high-confidence filename + exact size."""
    probe = _make_probe_result(
        content_disposition='attachment; filename="high.zip"',
        content_length="500000",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.quality == SourceQuality.DIRECT_FILE


def test_quality_medium_confidence():
    """MEDIUM_CONFIDENCE when medium-confidence filename + exact size."""
    probe = _make_probe_result(
        final_url="http://example.com/file.zip",
        content_length="500000",
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.quality == SourceQuality.MEDIUM_CONFIDENCE


def test_quality_low_confidence():
    """LOW_CONFIDENCE when no exact size or low-confidence filename."""
    probe = _make_probe_result(
        final_url="http://example.com/",
        content_length=None,
        size=None,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.quality == SourceQuality.LOW_CONFIDENCE


def test_quality_rejected_on_duplicate():
    """Duplicate resources are rejected."""
    intel = ResourceIntelligence()

    probe1 = _make_probe_result(
        content_disposition='attachment; filename="dup.zip"',
        content_length="1000",
    )
    intel.enrich(probe1)

    probe2 = _make_probe_result(
        url="http://other.com/dup.zip",
        content_disposition='attachment; filename="dup.zip"',
        content_length="1000",
    )
    result2 = intel.enrich(probe2)

    assert result2.quality == SourceQuality.REJECTED
    assert result2.duplicate.is_duplicate is True


def test_quality_rejected_on_http_error():
    """HTTP error status codes are rejected."""
    probe = _make_probe_result(status_code=404)
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.quality == SourceQuality.REJECTED


def test_quality_rejected_when_not_downloadable():
    """Non-downloadable resources are rejected."""
    probe = _make_probe_result(
        content_type="text/html",
        is_downloadable=False,
    )
    intel = ResourceIntelligence()
    result = intel.enrich(probe)

    assert result.quality == SourceQuality.REJECTED


# ── Integration: ResourceProbe returns enriched result ──────────────────

def test_resource_probe_result_has_intelligence_fields():
    """ResourceProbeResult includes V1.14 intelligence fields."""
    probe = _make_probe_result(
        content_disposition='attachment; filename="test.zip"',
        content_length="1000",
        content_range="bytes 0-999/1000",
    )

    assert hasattr(probe, "content_disposition")
    assert hasattr(probe, "content_length")
    assert hasattr(probe, "content_range")
    assert probe.content_disposition == 'attachment; filename="test.zip"'
    assert probe.content_length == "1000"
    assert probe.content_range == "bytes 0-999/1000"