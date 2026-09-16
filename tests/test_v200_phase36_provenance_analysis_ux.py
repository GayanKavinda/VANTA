"""V2.0 Phase 3.6 - Resource Provenance, Explanation and Analysis UX tests."""

from app.core.models import DownloadFile, ResolvedResource
from app.services.analysis_view import build_resource_view
from app.services.resource_intelligence import ResourceIntelligence
from app.sources.html_parser import parse_html
from app.ui.pages.home_page import ResourceDetailsDialog


def _resolution(**overrides):
	values = {
		"source_url": "https://page.example/item",
		"final_url": "https://cdn.example/video.mp4",
		"filename": "video.mp4",
		"size": 42 * 1024 * 1024,
		"content_type": "video/mp4; charset=binary",
		"supports_range": True,
		"score": 95,
		"confidence": "high",
		"reasons": ["downloadable MIME", "discovered through video element"],
		"filename_source": "content_disposition",
		"mime_source": "mime_type",
		"mime_media_type": "video/mp4",
		"mime_category": "video",
		"quality": "high_confidence",
		"size_source": "content_length",
		"element_type": "video",
		"discovery_attribute": "data-src",
		"html_type_hint": "video/mp4",
		"discovery_paths": ["video element", "lazy-loading"],
	}
	values.update(overrides)
	return ResolvedResource(**values)


def test_parser_preserves_provenance_for_supported_discovery_paths():
	parser = parse_html(
		'<img data-src="/image.jpg" srcset="/image.jpg 1x">'
		'<meta property="og:image" content="/image.jpg">'
		'<link rel="preload" as="image" href="/image.jpg">'
	)
	assert {candidate.discovery_attribute for candidate in parser.resources} == {
		"data-src", "srcset", "property", "preload"
	}


def test_resource_view_exposes_evidence_and_deduplicates_reasons():
	file = DownloadFile(name="video.mp4", url="https://cdn.example/video.mp4")
	resolution = _resolution(reasons=["same reason", "same reason", "HTML type hint: video/mp4"])
	view = build_resource_view(file, resolution)

	assert view.source_url == "https://page.example/item"
	assert view.final_url == "https://cdn.example/video.mp4"
	assert view.type_label == "video/mp4"
	assert view.size_label == "42.0 MB"
	assert view.filename_source == "content_disposition"
	assert view.discovery_paths == ["video element", "lazy-loading"]
	assert view.reasons == ["same reason", "HTML type hint: video/mp4"]


def test_unknown_analysis_fields_remain_neutral():
	file = DownloadFile(name="download", url="https://example.com/resource")
	view = build_resource_view(file, _resolution(
		filename_source="", mime_source="", mime_media_type=None,
		content_type=None,
		mime_category="", quality="", size=None, element_type="",
		discovery_attribute="", html_type_hint="", discovery_paths=[], reasons=[]
	))
	assert view.size_label == ""
	assert view.type_label == ""
	assert view.resolution_explanation == "Unknown"


def test_details_are_read_only_and_include_provenance():
	file = DownloadFile(name="video.mp4", url="https://cdn.example/video.mp4")
	view = build_resource_view(file, _resolution())
	rendered = ResourceDetailsDialog._render(view)

	assert "Source URL" in rendered
	assert "Resolved URL" in rendered
	assert "Server-provided filename" in rendered
	assert "HTTP Content-Type" in rendered
	assert "HTML type hint" in rendered
	assert "lazy-loading" in rendered
	assert "<input" not in rendered


def test_filename_intelligence_keeps_existing_precedence():
	probe = type("Probe", (), {
		"url": "https://example.com/download",
		"final_url": "https://example.com/download",
		"content_disposition": 'attachment; filename="server.zip"',
		"content_type": "application/zip",
		"size": 12,
		"content_length": None,
		"content_range": None,
	})()
	result = ResourceIntelligence().enrich(probe)
	assert result.filename.filename == "server.zip"
	assert result.filename.source == "content_disposition"