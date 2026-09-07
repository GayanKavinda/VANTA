import pytest
from pathlib import Path

from app.core.models import AnalysisResult, ResolvedResource
from app.services.analyzer import AnalyzerService, ResolutionContext
from app.services.resolver import Resolver
from app.services.resource_probe import ResourceProbe
from app.sources.direct import DirectDownloadAdapter
from app.sources.generic import GenericSourceAdapter
from app.sources.protected import ProtectedSourceAdapter
from app.services.source_detector import SourceDetector


class _FakeAdapter:
    def __init__(self, name, files=None, resolutions=None, status="ready"):
        self._name = name
        self._files = files or []
        self._resolutions = resolutions or []
        self._status = status

    @property
    def name(self):
        return self._name

    def can_handle(self, url):
        return True

    async def analyze(self, url):
        return AnalysisResult(
            title="Title",
            source=self._name,
            files=list(self._files),
            status=self._status,
        )

    def last_resolutions(self):
        return list(self._resolutions)


@pytest.mark.asyncio
async def test_analyze_returns_analysis_result_backward_compat():
    analyzer = AnalyzerService()
    analyzer._detector = SourceDetector.__new__(SourceDetector)
    analyzer._detector._adapters = [_FakeAdapter("Fake")]

    result = await analyzer.analyze("https://example.com/x")
    assert isinstance(result, AnalysisResult)
    assert result.source == "Fake"


@pytest.mark.asyncio
async def test_analyze_with_context_returns_resolution_context():
    analyzer = AnalyzerService()
    adapter = _FakeAdapter(
        "Fake",
        files=[],
        resolutions=[],
    )
    analyzer._detector = SourceDetector.__new__(SourceDetector)
    analyzer._detector._adapters = [adapter]

    result, context = await analyzer.analyze_with_context("https://example.com/x")
    assert isinstance(result, AnalysisResult)
    assert isinstance(context, ResolutionContext)
    assert context.resolutions == []


@pytest.mark.asyncio
async def test_analyze_with_context_exposes_adapter_resolutions():
    res = ResolvedResource(
        source_url="https://example.com/a.zip",
        final_url="https://example.com/a.zip",
        filename="a.zip",
        size=100,
        content_type="application/zip",
        supports_range=True,
        score=95,
        confidence="high",
        reasons=["file extension"],
    )
    from app.core.models import DownloadFile
    f = DownloadFile(name="a.zip", url="https://example.com/a.zip", size=100, content_type="application/zip")
    adapter = _FakeAdapter("Fake", files=[f], resolutions=[res])

    analyzer = AnalyzerService()
    analyzer._detector = SourceDetector.__new__(SourceDetector)
    analyzer._detector._adapters = [adapter]

    result, context = await analyzer.analyze_with_context("https://example.com/x")
    assert len(context.resolutions) == 1
    assert context.resolutions[0].confidence == "high"
    assert context.for_file(f) is res


@pytest.mark.asyncio
async def test_analyze_handles_adapter_failure_gracefully():
    class _Boom:
        @property
        def name(self):
            return "Boom"

        def can_handle(self, url):
            return True

        async def analyze(self, url):
            raise RuntimeError("kaboom")

        def last_resolutions(self):
            return []

    analyzer = AnalyzerService()
    analyzer._detector = SourceDetector.__new__(SourceDetector)
    analyzer._detector._adapters = [_Boom()]

    result, context = await analyzer.analyze_with_context("https://example.com/x")
    assert result.status == "error"
    assert context.resolutions == []


@pytest.mark.asyncio
async def test_analyze_handles_missing_adapter():
    analyzer = AnalyzerService()
    analyzer._detector = SourceDetector.__new__(SourceDetector)
    analyzer._detector._adapters = []

    result, context = await analyzer.analyze_with_context("https://example.com/x")
    assert result.status == "unsupported"
    assert context.resolutions == []


def test_base_adapter_default_last_resolutions_is_empty():
    from app.sources.base import BaseSourceAdapter
    from app.core.models import AnalysisResult

    class _Stub(BaseSourceAdapter):
        @property
        def name(self):
            return "Stub"

        def can_handle(self, url):
            return True

        async def analyze(self, url):
            return AnalysisResult(title="t", source="Stub", files=[], status="ready")

    assert _Stub().last_resolutions() == []


def test_real_adapters_inherit_default_last_resolutions():
    assert DirectDownloadAdapter().last_resolutions() == []
    assert ProtectedSourceAdapter().last_resolutions() == []


def test_generic_adapter_resolver_propagation():
    resolver = Resolver(
        probe=ResourceProbe(allow_private_networks=True),
        allow_private_networks=True,
    )
    adapter = GenericSourceAdapter(
        resolver=resolver,
        allow_private_networks=True,
    )
    assert adapter._resolver is resolver
    assert adapter._allow_private is True


def test_download_service_analyzer_property():
    from app.services.download_service import DownloadService
    from app.core.downloader import DownloadManager
    from app.core.file_manager import FileManager
    from app.services.analyzer import AnalyzerService

    analyzer = AnalyzerService()
    dm = DownloadManager()
    fm = FileManager(default_dir=Path("/tmp"))
    service = DownloadService(analyzer=analyzer, download_manager=dm, file_manager=fm)
    assert service.analyzer is analyzer