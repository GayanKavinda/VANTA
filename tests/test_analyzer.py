from app.sources.direct import DirectDownloadAdapter
from app.sources.protected import ProtectedSourceAdapter
from app.sources.generic import GenericSourceAdapter
from app.services.source_detector import SourceDetector


def test_protected_adapter_detects_login_urls():
    adapter = ProtectedSourceAdapter()
    assert adapter.can_handle("https://example.com/login") is True
    assert adapter.can_handle("https://account.example.com") is True
    assert adapter.can_handle("https://shop.com/signin?return=/") is True


def test_protected_adapter_rejects_direct():
    adapter = ProtectedSourceAdapter()
    assert adapter.can_handle("https://example.com/file.zip") is False


def test_source_detector_priority_direct_first():
    detector = SourceDetector()
    adapter = detector.detect("https://example.com/file.zip")
    assert adapter is not None
    assert adapter.name == "Direct Download"


def test_source_detector_protected():
    detector = SourceDetector()
    adapter = detector.detect("https://example.com/login")
    assert adapter is not None
    assert adapter.name == "Protected Source"


def test_direct_adapter_can_handle_direct_url():
    adapter = DirectDownloadAdapter()
    assert adapter.can_handle("https://example.com/file.zip") is True
    assert adapter.can_handle("https://example.com/document.pdf") is True
    assert adapter.can_handle("https://example.com/download/game.iso") is True


def test_direct_adapter_rejects_non_direct():
    adapter = DirectDownloadAdapter()
    assert adapter.can_handle("https://example.com") is False
    assert adapter.can_handle("https://example.com/download-page") is False
    assert adapter.can_handle("https://example.com/article?file=information") is False


def test_generic_adapter_handles_anything():
    adapter = GenericSourceAdapter()
    assert adapter.can_handle("https://any-site.com/page") is True


def test_source_detector_finds_adapter():
    detector = SourceDetector()
    adapter = detector.detect("https://example.com/file.zip")
    assert adapter is not None
    assert adapter.name == "Direct Download"


def test_source_detector_fallback_to_generic():
    detector = SourceDetector()
    adapter = detector.detect("https://example.com/page")
    assert adapter is not None
    assert adapter.name == "Generic Page"


def test_source_detector_routes_download_page_to_generic():
    detector = SourceDetector()
    adapter = detector.detect("https://example.com/articles/how-to-download-games")
    assert adapter is not None
    assert adapter.name == "Generic Page"


def test_source_detector_routes_query_with_file_to_generic():
    detector = SourceDetector()
    adapter = detector.detect("https://example.com/article?file=information")
    assert adapter is not None
    assert adapter.name == "Generic Page"
