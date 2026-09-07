import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

import pytest

from app.core.downloader import DownloadManager
from app.core.task_manager import DownloadErrorType, TaskStatus
from app.services.download_security import (
    RedirectWalkError,
    walk_redirects,
)
from app.utils.logger import setup_logger

setup_logger()


async def _wait_for_status(task, status, timeout=10.0):
    import asyncio
    interval = 0.05
    elapsed = 0.0
    while elapsed < timeout:
        if task.status == status:
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


def _start_server(port, handler_cls, serve_dir=None):
    original_chdir = os.getcwd()
    if serve_dir is not None:
        os.chdir(serve_dir)

    class _H(handler_cls):
        pass

    server = HTTPServer(("127.0.0.1", port), _H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, original_chdir


def _handler_for_static_file(file_bytes, content_type="application/octet-stream"):
    class _H(SimpleHTTPRequestHandler):
        body = file_bytes
        ctype = content_type

        def log_message(self, *args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", self.ctype)
            self.send_header("Content-Length", str(len(self.body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            range_header = self.headers.get("Range")
            if range_header:
                import re
                m = re.match(r"bytes=(\d+)-", range_header)
                if m:
                    start = int(m.group(1))
                    end = len(self.body) - 1
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(self.body)}")
                        self.end_headers()
                        return
                    if start < 0:
                        start = 0
                    self.send_response(206)
                    self.send_header("Content-Type", self.ctype)
                    self.send_header("Content-Length", str(len(self.body) - start))
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.body)}")
                    self.end_headers()
                    self.wfile.write(self.body[start:])
                    return
            self.send_response(200)
            self.send_header("Content-Type", self.ctype)
            self.send_header("Content-Length", str(len(self.body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(self.body)
    return _H


@pytest.fixture(scope="module")
def static_download_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("dl_static")
    payload = b"VANTA-DL" * 500
    (serve_dir / "file.bin").write_bytes(payload)

    handler_cls = _handler_for_static_file(payload)
    server, original_chdir = _start_server(18900, handler_cls, serve_dir)

    try:
        yield {
            "url": "http://127.0.0.1:18900/file.bin",
            "size": len(payload),
            "path": serve_dir / "file.bin",
        }
    finally:
        server.shutdown()
        os.chdir(original_chdir)


class _RedirectOnceHandler(SimpleHTTPRequestHandler):
    final_target: str = "/file.bin"

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        if self.path == self.final_target:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "4000")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return
        self.send_response(302)
        self.send_header("Location", self.final_target)
        self.end_headers()

    def do_GET(self):
        if self.path == self.final_target:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "4000")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(b"X" * 4000)
            return
        self.send_response(302)
        self.send_header("Location", self.final_target)
        self.end_headers()


@pytest.fixture(scope="module")
def redirect_once_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("dl_redirect_once")
    (serve_dir / "file.bin").write_bytes(b"X" * 4000)
    server, original_chdir = _start_server(18901, _RedirectOnceHandler, serve_dir)
    try:
        import time
        time.sleep(0.2)
        yield "http://127.0.0.1:18901/redirect"
    finally:
        server.shutdown()
        os.chdir(original_chdir)


_REMAINING = [0]


class _RedirectLoopHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        if _REMAINING[0] > 0:
            _REMAINING[0] -= 1
            self.send_response(302)
            self.send_header("Location", "/hop")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", "100")
        self.end_headers()

    def do_GET(self):
        self.do_HEAD()


@pytest.fixture(scope="module")
def redirect_loop_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("dl_redirect_loop")
    server, original_chdir = _start_server(18902, _RedirectLoopHandler, serve_dir)
    try:
        yield "http://127.0.0.1:18902/start"
    finally:
        server.shutdown()
        os.chdir(original_chdir)


class _CrossOriginRedirectHandler(SimpleHTTPRequestHandler):
    """Redirects to localhost (private) - same as unsafe destination."""
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/file.zip")
        self.end_headers()

    def do_GET(self):
        self.do_HEAD()


@pytest.fixture(scope="module")
def cross_origin_redirect_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("dl_cross")
    server, original_chdir = _start_server(18903, _CrossOriginRedirectHandler, serve_dir)
    try:
        yield "http://127.0.0.1:18903/start"
    finally:
        server.shutdown()
        os.chdir(original_chdir)


def _test_dm() -> DownloadManager:
    return DownloadManager(max_concurrent=1, allow_private_networks=True)


@pytest.mark.asyncio
async def test_download_walks_single_redirect(redirect_once_server, tmp_path):
    dm = _test_dm()
    dest = tmp_path / "out.bin"
    task = await dm.add_download(
        name="file.bin",
        source_url=redirect_once_server,
        download_url=redirect_once_server,
        destination=str(dest),
    )
    assert await _wait_for_status(task, TaskStatus.COMPLETED, timeout=15.0)
    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == 4000


@pytest.mark.asyncio
async def test_download_redirect_loop_fails_with_redirect_loop_error(redirect_loop_server, tmp_path):
    _REMAINING[0] = 20
    dm = _test_dm()
    dest = tmp_path / "loop.bin"
    task = await dm.add_download(
        name="loop.bin",
        source_url=redirect_loop_server,
        download_url=redirect_loop_server,
        destination=str(dest),
    )
    assert await _wait_for_status(task, TaskStatus.FAILED, timeout=15.0)
    assert task.error_type == DownloadErrorType.REDIRECT_LOOP


@pytest.mark.asyncio
async def test_download_rejects_unsafe_redirect(cross_origin_redirect_server, tmp_path):
    dm = DownloadManager(max_concurrent=1, allow_private_networks=False)
    dest = tmp_path / "unsafe.bin"
    task = await dm.add_download(
        name="unsafe.bin",
        source_url=cross_origin_redirect_server,
        download_url=cross_origin_redirect_server,
        destination=str(dest),
    )
    assert await _wait_for_status(task, TaskStatus.FAILED, timeout=15.0)
    assert task.error_type == DownloadErrorType.UNSAFE_REDIRECT


@pytest.mark.asyncio
async def test_download_redirect_to_loopback_fails():
    dm = DownloadManager(max_concurrent=1, allow_private_networks=False)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "out.bin")
        task = await dm.add_download(
            name="o.bin",
            source_url="http://127.0.0.1:1/nothing",
            download_url="http://127.0.0.1:1/nothing",
            destination=dest,
        )
        assert await _wait_for_status(task, TaskStatus.FAILED, timeout=10.0)
    assert task.error_type in (
        DownloadErrorType.UNSAFE_REDIRECT,
        DownloadErrorType.NETWORK,
    )


@pytest.mark.asyncio
async def test_download_normal_file_still_works(static_download_server, tmp_path):
    dm = _test_dm()
    dest = tmp_path / "ok.bin"
    task = await dm.add_download(
        name="ok.bin",
        source_url=static_download_server["url"],
        download_url=static_download_server["url"],
        destination=str(dest),
    )
    assert await _wait_for_status(task, TaskStatus.COMPLETED, timeout=15.0)
    assert dest.stat().st_size == static_download_server["size"]


@pytest.mark.asyncio
async def test_download_redirect_walk_helper_returns_final_url(redirect_once_server):
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        def factory(url=None):
            target = url or redirect_once_server
            return client.build_request("GET", target)

        walk = await walk_redirects(
            client, factory, max_redirects=3,
            allow_private_networks=True,
        )
        assert walk.hops >= 0
        assert walk.final_url.endswith("/file.bin")
        assert walk.changed_host is False
        await walk.response.aclose()


@pytest.mark.asyncio
async def test_download_redirect_walk_helper_raises_on_too_many_redirects(redirect_loop_server):
    _REMAINING[0] = 20
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        def factory(url=None):
            target = url or redirect_loop_server
            return client.build_request("GET", target)

        with pytest.raises(RedirectWalkError) as exc:
            await walk_redirects(
                client, factory, max_redirects=3,
                allow_private_networks=True,
            )
        assert exc.value.kind == "redirect_loop"


@pytest.mark.asyncio
async def test_download_redirect_walk_helper_detects_host_change():
    from app.services.url_security import is_safe_redirect

    decision = is_safe_redirect(
        "https://example.com/start",
        "https://different.example.com/file.zip",
    )
    assert decision.is_safe is True

    decision_blocked = is_safe_redirect(
        "https://example.com/start",
        "https://different.example.com/file.zip",
        blocked_hosts=("different.example.com",),
    )
    assert decision_blocked.is_safe is False
    assert "different.example.com" in decision_blocked.reason


def test_redirect_loop_error_type_exists():
    assert DownloadErrorType.REDIRECT_LOOP is not None
    assert DownloadErrorType.UNSAFE_REDIRECT is not None