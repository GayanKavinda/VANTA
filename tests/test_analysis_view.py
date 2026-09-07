from app.core.models import AnalysisResult, DownloadFile, ResolvedResource
from app.services.analysis_view import (
    AnalysisViewModel,
    ResourceView,
    build_resource_view,
    build_view_model,
    format_content_type,
    format_size,
)
from app.services.analyzer import ResolutionContext


def test_format_size_bytes():
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"


def test_format_size_kb():
    assert format_size(1024) == "1.0 KB"
    assert format_size(1536) == "1.5 KB"


def test_format_size_mb():
    val = 1024 * 1024 * 4
    assert format_size(val) == "4.0 MB"


def test_format_size_gb():
    val = 1024 * 1024 * 1024 * 2
    assert format_size(val) == "2.0 GB"


def test_format_size_none_returns_empty():
    assert format_size(None) == ""


def test_format_size_negative_returns_empty():
    assert format_size(-1) == ""


def test_format_content_type_strips_parameters():
    assert format_content_type("application/zip; charset=binary") == "application/zip"
    assert format_content_type(None) == ""
    assert format_content_type("") == ""


def test_build_resource_view_without_resolution_is_low_confidence():
    f = DownloadFile(name="x.zip", url="https://example.com/x.zip")
    rv = build_resource_view(f, None)
    assert rv.confidence == "low"
    assert rv.confidence_label == "LOW"
    assert rv.reasons == []
    assert rv.size_label == ""


def test_build_resource_view_with_resolution_uses_metadata():
    f = DownloadFile(
        name="game.zip",
        url="https://example.com/game.zip",
        size=4 * 1024 * 1024 * 1024,
        content_type="application/zip",
    )
    res = ResolvedResource(
        source_url="https://example.com/game.zip",
        final_url="https://example.com/game.zip",
        filename="game.zip",
        size=f.size,
        content_type="application/zip",
        supports_range=True,
        score=95,
        confidence="high",
        reasons=["file extension", "filename from URL path"],
    )
    rv = build_resource_view(f, res)
    assert rv.confidence == "high"
    assert rv.confidence_label == "HIGH"
    assert rv.score == 95
    assert rv.reasons == ["file extension", "filename from URL path"]
    assert "GB" in rv.size_label
    assert rv.type_label == "application/zip"


def test_build_view_model_handles_empty_files():
    result = AnalysisResult(title="Nothing", source="Generic Page", files=[], status="unsupported")
    vm = build_view_model(result)
    assert vm.has_resources is False
    assert vm.is_unsupported is True
    assert vm.is_error is False


def test_build_view_model_marks_error_status():
    result = AnalysisResult(title="Boom", source="Generic Page", files=[], status="error")
    vm = build_view_model(result)
    assert vm.is_error is True


def test_build_view_model_links_resolution_to_file():
    f1 = DownloadFile(name="a.zip", url="https://example.com/a.zip")
    f2 = DownloadFile(name="b.zip", url="https://example.com/b.zip")
    res1 = ResolvedResource(
        source_url="https://example.com/a.zip",
        final_url="https://example.com/a.zip",
        filename="a.zip", size=None, content_type=None, supports_range=None,
        score=95, confidence="high",
    )
    context = ResolutionContext(resolutions=[res1])

    result = AnalysisResult(title="Page", source="Generic Page", files=[f1, f2], status="ready")
    vm = build_view_model(result, context)
    assert len(vm.resources) == 2
    assert vm.resources[0].confidence == "high"
    assert vm.resources[1].confidence == "low"


def test_build_view_model_ready_with_resources_is_ready():
    f = DownloadFile(name="a.zip", url="https://example.com/a.zip", size=100)
    result = AnalysisResult(title="Page", source="Generic Page", files=[f], status="ready")
    vm = build_view_model(result)
    assert vm.is_ready is True
    assert vm.has_resources is True


def test_resolution_context_for_file_matches_final_url():
    f = DownloadFile(name="x", url="https://example.com/x.zip")
    res = ResolvedResource(
        source_url="https://example.com/dl?id=1",
        final_url="https://example.com/x.zip",
        filename="x.zip", size=None, content_type=None, supports_range=None,
        score=80, confidence="medium",
    )
    ctx = ResolutionContext(resolutions=[res])
    assert ctx.for_file(f) is res


def test_resolution_context_for_file_returns_none_for_unknown():
    ctx = ResolutionContext()
    f = DownloadFile(name="x", url="https://example.com/x.zip")
    assert ctx.for_file(f) is None