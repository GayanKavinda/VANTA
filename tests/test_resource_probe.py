import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import pytest

from app.core.models import ResourceProbeResult
from app.services.resource_probe import ResourceProbe, probe_many
from app.sources.http_headers import (
    classify_downloadability,
    extract_filename_from_content_disposition,
    extract_filename_from_url,
    normalize_media_type,
    parse_accept_ranges,
    safe_content_length,
)
from app.utils.logger import setup_logger

setup_logger()


class _ProbeHandler(SimpleHTTPRequestHandler):
    serve_dir: str = ""
    serve_dir_real: str = ""

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        path = urlparse(path).path
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = path.lstrip("/")
        return os.path.join(self.serve_dir, path)

    def do_HEAD(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        size = os.path.getsize(path)
        ctype = self.guess_type(path)
        if path.endswith(".zip"):
            ctype = "application/zip"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
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
        size = os.path.getsize(path)
        ctype = self.guess_type(path)
        if path.endswith(".zip"):
            ctype = "application/zip"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
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


class _NoHeadHandler(SimpleHTTPRequestHandler):
    serve_dir: str = ""

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        path = urlparse(path).path
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = path.lstrip("/")
        return os.path.join(self.serve_dir, path)

    def do_HEAD(self):
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
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


class _RedirectHandler(SimpleHTTPRequestHandler):
    serve_dir: str = ""

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        path = urlparse(path).path
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = path.lstrip("/")
        return os.path.join(self.serve_dir, path)

    def do_HEAD(self):
        path = self.translate_path(self.path)
        if path.endswith("redirect"):
            self.send_response(302)
            self.send_header("Location", "/actual.zip")
            self.end_headers()
            return
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(size))
        self.end_headers()


class _StaticResponseHandler(SimpleHTTPRequestHandler):
    status: int = 200
    body: bytes = b""
    content_type: str = "application/zip"
    content_length_header: str = ""
    accept_ranges: str = ""

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(self.status)
        self.send_header("Content-Type", self.content_type)
        if self.content_length_header:
            self.send_header("Content-Length", self.content_length_header)
        if self.accept_ranges:
            self.send_header("Accept-Ranges", self.accept_ranges)
        self.end_headers()

    def do_GET(self):
        self.do_HEAD()
        try:
            self.wfile.write(self.body)
        except BrokenPipeError:
            pass


def _start_simple_server(port, handler_cls, serve_dir=None):
    original_chdir = os.getcwd()
    if serve_dir is not None:
        os.chdir(serve_dir)
        serve_dir_str = str(serve_dir)
    else:
        serve_dir_str = ""

    class _H(handler_cls):
        pass

    _H.serve_dir = serve_dir_str

    server = HTTPServer(("127.0.0.1", port), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, original_chdir


@pytest.fixture(scope="module")
def probe_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("probe_serve")
    (serve_dir / "file.zip").write_bytes(b"PK" * 100)
    server, original_chdir = _start_simple_server(18600, _ProbeHandler, serve_dir)
    yield {"url": "http://127.0.0.1:18600/file.zip", "size": 200}
    server.shutdown()
    os.chdir(original_chdir)


@pytest.fixture(scope="module")
def probe_nohead_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("probe_nohead")
    (serve_dir / "nohead.zip").write_bytes(b"NH" * 50)
    server, original_chdir = _start_simple_server(18601, _NoHeadHandler, serve_dir)
    yield {"url": "http://127.0.0.1:18601/nohead.zip", "size": 100}
    server.shutdown()
    os.chdir(original_chdir)


@pytest.fixture(scope="module")
def probe_redirect_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("probe_redirect")
    (serve_dir / "actual.zip").write_bytes(b"AA" * 50)
    server, original_chdir = _start_simple_server(18602, _RedirectHandler, serve_dir)
    yield {"url": "http://127.0.0.1:18602/redirect"}
    server.shutdown()
    os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_head_success(probe_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_server["url"])
    assert result.status_code == 200
    assert result.is_downloadable is True


@pytest.mark.asyncio
async def test_probe_captures_content_type(probe_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_server["url"])
    assert "application/zip" in (result.content_type or "").lower()


@pytest.mark.asyncio
async def test_probe_captures_content_length(probe_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_server["url"])
    assert result.size == probe_server["size"]


@pytest.mark.asyncio
async def test_probe_filename_from_content_disposition(probe_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_server["url"])
    assert result.filename == "server.zip"


@pytest.mark.asyncio
async def test_probe_accept_ranges_bytes(probe_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_server["url"])
    assert result.supports_range is True


@pytest.mark.asyncio
async def test_probe_handles_malformed_content_length():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = "application/zip"
        content_length_header = "unknown"
        accept_ranges = ""

    server, original_chdir = _start_simple_server(18610, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18610/anything")
        assert result.size is None
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_handles_negative_content_length():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = "application/zip"
        content_length_header = "-5"
        accept_ranges = ""

    server, original_chdir = _start_simple_server(18611, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18611/anything")
        assert result.size is None
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_missing_accept_ranges_returns_none():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = "application/zip"
        content_length_header = "100"
        accept_ranges = ""

    server, original_chdir = _start_simple_server(18612, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18612/anything")
        assert result.supports_range is None
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_accept_ranges_none_returns_false():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = "application/zip"
        content_length_header = "100"
        accept_ranges = "none"

    server, original_chdir = _start_simple_server(18613, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18613/anything")
        assert result.supports_range is False
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_html_is_not_downloadable():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = "text/html; charset=utf-8"
        content_length_header = "100"
        accept_ranges = ""
        body = b"<html><body>Page</body></html>"

    server, original_chdir = _start_simple_server(18614, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18614/page")
        assert result.is_downloadable is False
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_octet_stream_is_downloadable():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = "application/octet-stream"
        content_length_header = "100"
        accept_ranges = "bytes"

    server, original_chdir = _start_simple_server(18615, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18615/blob")
        assert result.is_downloadable is True
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_zip_is_downloadable(probe_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_server["url"])
    assert result.is_downloadable is True


@pytest.mark.asyncio
async def test_probe_missing_content_type_uses_extension_fallback():
    class _H(_StaticResponseHandler):
        status = 200
        content_type = ""
        content_length_header = "100"
        accept_ranges = ""

    server, original_chdir = _start_simple_server(18616, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18616/file.zip")
        assert result.is_downloadable is True
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_head_405_falls_back_to_get(probe_nohead_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_nohead_server["url"])
    assert result.status_code == 200
    assert result.filename == "from_get.zip"


@pytest.mark.asyncio
async def test_probe_connection_failure_returns_safe_result():
    result = await ResourceProbe().probe("http://127.0.0.1:1/nothing")
    assert isinstance(result, ResourceProbeResult)
    assert result.is_downloadable is False
    assert result.status_code == 0


@pytest.mark.asyncio
async def test_probe_http_404_does_not_crash():
    class _H(_StaticResponseHandler):
        status = 404
        content_type = "text/plain"
        content_length_header = "0"
        accept_ranges = ""

    server, original_chdir = _start_simple_server(18617, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18617/missing")
        assert result.status_code == 404
        assert result.is_downloadable is False
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_http_403_does_not_crash():
    class _H(_StaticResponseHandler):
        status = 403
        content_type = "text/plain"
        content_length_header = "0"
        accept_ranges = ""

    server, original_chdir = _start_simple_server(18618, _H)
    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18618/blocked")
        assert result.status_code == 403
        assert result.is_downloadable is False
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_final_url_after_redirect(probe_redirect_server):
    probe = ResourceProbe()
    result = await probe.probe(probe_redirect_server["url"])
    assert result.status_code == 200
    assert result.final_url.endswith("/actual.zip")
    assert result.url == probe_redirect_server["url"]


@pytest.mark.asyncio
async def test_probe_get_fallback_does_not_consume_full_body():
    import pathlib

    class _CountingHandler(SimpleHTTPRequestHandler):
        serve_dir: str = ""
        bytes_written: int = 0

        def log_message(self, *args):
            pass

        def translate_path(self, path):
            path = urlparse(path).path
            path = path.split("?", 1)[0].split("#", 1)[0]
            path = path.lstrip("/")
            return os.path.join(self.serve_dir, path)

        def do_HEAD(self):
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.translate_path(self.path)
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                'attachment; filename="big.zip"',
            )
            self.end_headers()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        _CountingHandler.bytes_written += len(chunk)
                    except BrokenPipeError:
                        break

    serve_dir = pathlib.Path(os.environ.get("TEMP", "/tmp")) / "probe_peek"
    serve_dir.mkdir(exist_ok=True)
    big_file = serve_dir / "big.bin"
    big_file.write_bytes(b"X" * (5 * 1024 * 1024))

    original_chdir = os.getcwd()
    os.chdir(serve_dir)
    _CountingHandler.serve_dir = str(serve_dir)
    _CountingHandler.bytes_written = 0

    server = HTTPServer(("127.0.0.1", 18619), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        result = await ResourceProbe().probe("http://127.0.0.1:18619/big.bin")
        assert result.status_code == 200
        assert result.is_downloadable is True
        assert _CountingHandler.bytes_written < 5 * 1024 * 1024
    finally:
        server.shutdown()
        os.chdir(original_chdir)


@pytest.mark.asyncio
async def test_probe_many_bounded_concurrency():
    urls = [f"http://127.0.0.1:18600/file.zip" for _ in range(8)]
    results = await probe_many(urls, concurrency=2)
    assert len(results) == 8
    assert all(r.status_code == 200 for r in results)


@pytest.mark.asyncio
async def test_probe_many_handles_failures():
    urls = [
        "http://127.0.0.1:18600/file.zip",
        "http://127.0.0.1:1/nothing",
    ]
    results = await probe_many(urls)
    assert len(results) == 2
    assert results[0].is_downloadable is True
    assert results[1].is_downloadable is False


def test_safe_content_length_valid():
    assert safe_content_length("1234") == 1234


def test_safe_content_length_invalid():
    assert safe_content_length("unknown") is None
    assert safe_content_length("abc") is None
    assert safe_content_length("") is None
    assert safe_content_length(None) is None
    assert safe_content_length("-5") is None


def test_extract_filename_from_content_disposition_none():
    assert extract_filename_from_content_disposition(None) is None
    assert extract_filename_from_content_disposition("") is None


def test_extract_filename_from_url_none():
    assert extract_filename_from_url(None) is None
    assert extract_filename_from_url("") is None


def test_normalize_media_type():
    assert normalize_media_type("application/zip; charset=binary") == "application/zip"
    assert normalize_media_type(None) is None
    assert normalize_media_type("") is None


def test_parse_accept_ranges():
    assert parse_accept_ranges("bytes") is True
    assert parse_accept_ranges("none") is False
    assert parse_accept_ranges(None) is None
    assert parse_accept_ranges("") is None
    assert parse_accept_ranges("weird") is None


def test_classify_downloadability_html():
    assert classify_downloadability(media_type="text/html", url="https://x", status_code=200) is False


def test_classify_downloadability_pdf():
    assert classify_downloadability(media_type="application/pdf", url="https://x", status_code=200) is True


def test_classify_downloadability_status_error():
    assert classify_downloadability(media_type="application/zip", url="https://x", status_code=404) is False


def test_classify_downloadability_missing_type_with_extension():
    assert classify_downloadability(media_type=None, url="https://x/file.zip", status_code=200) is True


def test_classify_downloadability_application_zip():
    assert classify_downloadability(media_type="application/zip", url="https://x", status_code=200) is True


def test_classify_downloadability_application_octet_stream():
    assert classify_downloadability(media_type="application/octet-stream", url="https://x", status_code=200) is True


def test_classify_downloadability_image_png():
    assert classify_downloadability(media_type="image/png", url="https://x", status_code=200) is True


def test_classify_downloadability_video_mp4():
    assert classify_downloadability(media_type="video/mp4", url="https://x", status_code=200) is True


def test_classify_downloadability_audio_mpeg():
    assert classify_downloadability(media_type="audio/mpeg", url="https://x", status_code=200) is True


def test_classify_downloadability_application_javascript_is_false():
    assert classify_downloadability(media_type="application/javascript", url="https://x", status_code=200) is False


def test_classify_downloadability_application_json_is_false():
    assert classify_downloadability(media_type="application/json", url="https://x", status_code=200) is False


def test_classify_downloadability_application_wasm_is_false():
    assert classify_downloadability(media_type="application/wasm", url="https://x", status_code=200) is False


def test_classify_downloadability_unknown_application_no_extension_is_false():
    assert classify_downloadability(media_type="application/x-custom", url="https://x/endpoint", status_code=200) is False


def test_classify_downloadability_unknown_with_zip_extension_is_true():
    assert classify_downloadability(media_type=None, url="https://x/file.zip", status_code=200) is True


def test_classify_downloadability_xhtml_is_false():
    assert classify_downloadability(media_type="application/xhtml+xml", url="https://x", status_code=200) is False