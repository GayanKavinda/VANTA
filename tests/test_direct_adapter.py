import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import pytest

from app.sources.direct import DirectDownloadAdapter
from app.utils.logger import setup_logger

setup_logger()


class _DirectFileHandler(SimpleHTTPRequestHandler):
    serve_dir: str = ""

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        file_size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(file_size))
        self.send_header(
            "Content-Disposition",
            'attachment; filename="server.zip"',
        )
        self.end_headers()

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        file_size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(file_size))
        self.send_header(
            "Content-Disposition",
            'attachment; filename="server.zip"',
        )
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break

    def translate_path(self, path):
        path = urlparse(path).path
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = path.lstrip("/")
        return os.path.join(self.serve_dir, path)


class _NoHeadHandler(SimpleHTTPRequestHandler):
    serve_dir: str = ""

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(405, "Method Not Allowed")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        file_size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(file_size))
        self.send_header(
            "Content-Disposition",
            'attachment; filename="from_get.zip"',
        )
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break

    def translate_path(self, path):
        path = urlparse(path).path
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = path.lstrip("/")
        return os.path.join(self.serve_dir, path)


def _start_server(port, handler_cls, serve_dir):
    original_chdir = os.getcwd()
    os.chdir(serve_dir)
    serve_dir_str = str(serve_dir)

    class _H(handler_cls):
        pass

    _H.serve_dir = serve_dir_str

    server = HTTPServer(("127.0.0.1", port), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, original_chdir


@pytest.fixture(scope="module")
def head_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("direct_serve")
    (serve_dir / "actual.zip").write_bytes(b"ZIPDATA" * 100)
    server, original_chdir = _start_server(18500, _DirectFileHandler, serve_dir)
    yield {
        "url": "http://127.0.0.1:18500/actual.zip",
        "dir": serve_dir,
    }
    server.shutdown()
    os.chdir(original_chdir)


@pytest.fixture(scope="module")
def nohead_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("direct_nohead")
    (serve_dir / "nohead.zip").write_bytes(b"NH" * 50)
    server, original_chdir = _start_server(18501, _NoHeadHandler, serve_dir)
    yield {
        "url": "http://127.0.0.1:18501/nohead.zip",
        "dir": serve_dir,
    }
    server.shutdown()
    os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_direct_adapter_analyze_returns_ready(head_server):
    adapter = DirectDownloadAdapter()
    result = await adapter.analyze(head_server["url"])
    assert result.status == "ready"
    assert result.source == "Direct Download"
    assert len(result.files) == 1


@pytest.mark.asyncio
async def test_direct_adapter_extracts_filename_from_content_disposition(head_server):
    adapter = DirectDownloadAdapter()
    result = await adapter.analyze(head_server["url"])
    assert result.files[0].name == "server.zip"


@pytest.mark.asyncio
async def test_direct_adapter_preserves_content_length(head_server):
    adapter = DirectDownloadAdapter()
    result = await adapter.analyze(head_server["url"])
    assert result.files[0].size == 700


@pytest.mark.asyncio
async def test_direct_adapter_captures_content_type(head_server):
    adapter = DirectDownloadAdapter()
    result = await adapter.analyze(head_server["url"])
    assert result.files[0].content_type == "application/zip"


@pytest.mark.asyncio
async def test_direct_adapter_fallback_when_head_returns_405(nohead_server):
    adapter = DirectDownloadAdapter()
    result = await adapter.analyze(nohead_server["url"])
    assert result.status == "ready"
    assert result.files[0].name == "from_get.zip"
    assert result.files[0].size == 100


@pytest.mark.asyncio
async def test_direct_adapter_does_not_consume_full_file(head_server):
    adapter = DirectDownloadAdapter()

    class CountingClient:
        def __init__(self, *args, **kwargs):
            self.head_called = False
            self.get_stream_iterated = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def head(self, url, **kwargs):
            import httpx
            self.head_called = True
            return httpx.Response(
                200,
                headers={
                    "content-length": "100",
                    "content-disposition": 'attachment; filename="x.zip"',
                },
                request=httpx.Request("HEAD", url),
            )

        async def get(self, url, **kwargs):
            self.get_stream_iterated = True
            raise AssertionError("GET should not be called when HEAD succeeds")

    import httpx
    original_client = httpx.AsyncClient
    httpx.AsyncClient = CountingClient
    try:
        result = await adapter.analyze("http://example.com/x.zip")
    finally:
        httpx.AsyncClient = original_client

    assert result.files[0].name == "x.zip"
    assert result.files[0].size == 100


def test_direct_adapter_filename_from_url_path():
    from app.sources.http_headers import extract_filename_from_url
    fn = extract_filename_from_url("https://example.com/files/game%20v2.zip")
    assert fn == "game v2.zip"


def test_direct_adapter_filename_fallback():
    from app.sources.http_headers import extract_filename_from_url
    fn = extract_filename_from_url("https://example.com/")
    assert fn is None


def test_direct_adapter_filename_plain():
    from app.sources.http_headers import extract_filename_from_content_disposition
    fn = extract_filename_from_content_disposition('attachment; filename="plain.zip"')
    assert fn == "plain.zip"


def test_direct_adapter_filename_rfc5987_encoded():
    from app.sources.http_headers import extract_filename_from_content_disposition
    cd = "attachment; filename*=UTF-8''game%20archive.zip"
    fn = extract_filename_from_content_disposition(cd)
    assert fn == "game archive.zip"


def test_direct_adapter_filename_prefers_filename_star_over_plain():
    from app.sources.http_headers import extract_filename_from_content_disposition
    cd = "attachment; filename=\"plain.zip\"; filename*=UTF-8''encoded.zip"
    fn = extract_filename_from_content_disposition(cd)
    assert fn == "encoded.zip"


def test_direct_adapter_can_handle_webpages_with_download_word():
    adapter = DirectDownloadAdapter()
    assert adapter.can_handle("https://example.com/articles/how-to-download-games") is False
    assert adapter.can_handle("https://example.com/page?file=information") is False
    assert adapter.can_handle("https://example.com/download-page") is False


def test_direct_adapter_can_handle_extension_still_works():
    adapter = DirectDownloadAdapter()
    assert adapter.can_handle("https://example.com/files/game.zip") is True
    assert adapter.can_handle("https://example.com/manual.pdf") is True
    assert adapter.can_handle("https://example.com/download/game.iso") is True


@pytest.mark.asyncio
async def test_direct_adapter_handles_malformed_content_length():
    import httpx

    real_response = httpx.Response(
        200,
        headers={
            "content-disposition": 'attachment; filename="x.zip"',
            "content-length": "unknown",
            "content-type": "application/zip",
        },
        request=httpx.Request("HEAD", "https://example.com/file.zip"),
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def head(self, url, **kwargs):
            return real_response

    original_client = httpx.AsyncClient
    httpx.AsyncClient = FakeClient
    try:
        result = await DirectDownloadAdapter().analyze("https://example.com/file.zip")
    finally:
        httpx.AsyncClient = original_client

    assert result.status == "ready"
    assert result.files[0].size is None
    assert result.files[0].name == "x.zip"


@pytest.mark.asyncio
async def test_direct_adapter_handles_negative_content_length():
    import httpx

    real_response = httpx.Response(
        200,
        headers={
            "content-disposition": 'attachment; filename="x.zip"',
            "content-length": "-5",
            "content-type": "application/zip",
        },
        request=httpx.Request("HEAD", "https://example.com/file.zip"),
    )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def head(self, url, **kwargs):
            return real_response

    original_client = httpx.AsyncClient
    httpx.AsyncClient = FakeClient
    try:
        result = await DirectDownloadAdapter().analyze("https://example.com/file.zip")
    finally:
        httpx.AsyncClient = original_client

    assert result.files[0].size is None