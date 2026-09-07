import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.services.analyzer import AnalyzerService
from app.services.download_service import DownloadService
from app.sources.generic import GenericSourceAdapter
from app.sources.html_parser import parse_html
from app.sources.link_classifier import is_download_candidate
from app.utils.logger import setup_logger

setup_logger()


class _PageHandler(SimpleHTTPRequestHandler):
    pages: dict[str, tuple[int, str, bytes]] = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path in self.pages:
            status, content_type, body = self.pages[path]
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "Not found")


def _make_server(port, pages):
    pages_copy = dict(pages)

    class _IsolatedHandler(_PageHandler):
        pass

    _IsolatedHandler.pages = pages_copy

    server = HTTPServer(("127.0.0.1", port), _IsolatedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield server

    server.shutdown()


@pytest.fixture(scope="module")
def page_server_with_links():
    gen = _make_server(18400, {
        "/page.html": (
            200,
            "text/html",
            b"""<!doctype html>
<html>
<head><title>Game Page</title></head>
<body>
<a href="https://example.com/game.zip">Game Archive</a>
<a href="/patch.zip">Patch</a>
<a href="https://example.com/about">About</a>
<a href="javascript:void(0)">Click</a>
<a href="mailto:x@y.com">Email</a>
<a href="#section">Anchor</a>
<a href="https://example.com/bonus.zip">Bonus</a>
<a href="https://example.com/game.zip">Game Archive Duplicate</a>
</body>
</html>""",
        ),
    })
    server = next(gen)
    yield "http://127.0.0.1:18400/page.html"
    next(gen, None)


@pytest.fixture(scope="module")
def page_server_no_links():
    gen = _make_server(18401, {
        "/empty.html": (
            200,
            "text/html",
            b"<html><head><title>Empty</title></head><body><p>No links</p></body></html>",
        ),
    })
    server = next(gen)
    yield "http://127.0.0.1:18401/empty.html"
    next(gen, None)


@pytest.fixture(scope="module")
def page_server_relative_only():
    gen = _make_server(18402, {
        "/page2.html": (
            200,
            "text/html",
            b'<html><head><title>Relative</title></body><a href="archive.zip">Rel</a></body></html>',
        ),
    })
    server = next(gen)
    yield "http://127.0.0.1:18402/page2.html"
    next(gen, None)


@pytest.fixture(scope="module")
def page_server_404():
    gen = _make_server(18403, {
        "/missing.html": (404, "text/html", b"Not Found"),
    })
    server = next(gen)
    yield "http://127.0.0.1:18403/missing.html"
    next(gen, None)


@pytest.fixture(scope="module")
def page_server_non_html():
    gen = _make_server(18404, {
        "/file.json": (200, "application/json", b'{"hello":"world"}'),
    })
    server = next(gen)
    yield "http://127.0.0.1:18404/file.json"
    next(gen, None)


@pytest.mark.asyncio
async def test_generic_adapter_extracts_title(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    assert result.title == "Game Page"
    assert result.source == "Generic Page"


@pytest.mark.asyncio
async def test_generic_adapter_discovers_links(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    urls = [f.url for f in result.files]
    assert any("game.zip" in u for u in urls)
    assert any("patch.zip" in u for u in urls)
    assert any("bonus.zip" in u for u in urls)


@pytest.mark.asyncio
async def test_generic_adapter_ignores_javascript_mailto_fragment(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    urls = [f.url for f in result.files]
    for u in urls:
        assert not u.lower().startswith("javascript:")
        assert not u.lower().startswith("mailto:")
        assert not u.startswith("#")


@pytest.mark.asyncio
async def test_generic_adapter_ignores_non_download_links(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    urls = [f.url for f in result.files]
    for u in urls:
        assert is_download_candidate(u)


@pytest.mark.asyncio
async def test_generic_adapter_deduplicates(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    urls = [f.url for f in result.files]
    assert len(urls) == len(set(urls))


@pytest.mark.asyncio
async def test_generic_adapter_returns_multiple_files(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    assert result.status == "ready"
    assert len(result.files) >= 3


@pytest.mark.asyncio
async def test_generic_adapter_no_links_returns_unsupported(page_server_no_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_no_links)
    assert result.status == "unsupported"
    assert result.files == []


@pytest.mark.asyncio
async def test_generic_adapter_http_error_returns_error(page_server_404):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_404)
    assert result.status == "error"
    assert result.files == []


@pytest.mark.asyncio
async def test_generic_adapter_non_html_returns_unsupported(page_server_non_html):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_non_html)
    assert result.status == "unsupported"
    assert result.files == []


@pytest.mark.asyncio
async def test_generic_adapter_resolves_relative_urls(page_server_relative_only):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_relative_only)
    assert result.status == "ready"
    assert len(result.files) == 1
    assert result.files[0].url.endswith("/archive.zip")


@pytest.mark.asyncio
async def test_generic_adapter_preserves_link_text(page_server_with_links):
    adapter = GenericSourceAdapter()
    result = await adapter.analyze(page_server_with_links)
    names = {f.name for f in result.files}
    assert "Patch" in names or "Bonus" in names


@pytest.mark.asyncio
async def test_generic_adapter_can_handle_http():
    adapter = GenericSourceAdapter()
    assert adapter.can_handle("https://example.com/page") is True
    assert adapter.can_handle("http://example.com/page") is True
    assert adapter.can_handle("ftp://example.com") is False
    assert adapter.can_handle("") is False


def test_html_parser_works_on_generic_page_html():
    html = '<html><title>T</title><body><a href="x.zip">X</a></body></html>'
    parser = parse_html(html)
    assert parser.title == "T"
    assert len(parser.links) == 1


@pytest.mark.asyncio
async def test_download_service_start_file_download_creates_task(tmp_path):
    file_manager = FileManager(default_dir=tmp_path)
    download_manager = DownloadManager(max_concurrent=1)
    analyzer = AnalyzerService()
    service = DownloadService(analyzer, download_manager, file_manager)

    file = DownloadFile(
        name="test.zip",
        url="https://example.com/test.zip",
        size=1024,
        content_type="application/zip",
    )

    task = await service.start_file_download(
        source_url="https://example.com/page",
        file=file,
    )

    assert task is not None
    assert task.name == "test.zip"
    assert task.source_url == "https://example.com/page"
    assert task.download_url == "https://example.com/test.zip"
    assert task.id != ""


@pytest.mark.asyncio
async def test_download_service_start_file_download_destination_uses_filename(tmp_path):
    file_manager = FileManager(default_dir=tmp_path / "dl")
    download_manager = DownloadManager(max_concurrent=1)
    analyzer = AnalyzerService()
    service = DownloadService(analyzer, download_manager, file_manager)

    file = DownloadFile(name="game.zip", url="https://example.com/game.zip")

    task = await service.start_file_download(
        source_url="https://example.com/page",
        file=file,
        destination=tmp_path / "custom",
    )

    assert task.destination.endswith("game.zip")
    assert str(tmp_path / "custom") in task.destination


@pytest.mark.asyncio
async def test_download_service_start_file_download_sanitizes_filename(tmp_path):
    file_manager = FileManager(default_dir=tmp_path)
    download_manager = DownloadManager(max_concurrent=1)
    analyzer = AnalyzerService()
    service = DownloadService(analyzer, download_manager, file_manager)

    file = DownloadFile(name="../../etc/passwd", url="https://example.com/x")

    task = await service.start_file_download(
        source_url="https://example.com/page",
        file=file,
    )

    dest_name = Path(task.destination).name
    assert ".." not in dest_name
    assert "/" not in dest_name
    assert "\\" not in dest_name