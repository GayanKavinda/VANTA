import asyncio
import builtins
import os
import shutil
import threading
import time
import unittest.mock
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from app.core.downloader import DownloadManager
from app.core.task_manager import DownloadErrorType, TaskStatus
from app.utils.logger import setup_logger

setup_logger()


class _BaseFileHandler(SimpleHTTPRequestHandler):
    """Base handler that serves files from a specific directory with Range support."""
    serve_dir: str = ""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        import urllib.parse
        path = urllib.parse.unquote(path)
        path = path.split("?", 1)[0].split("#", 1)[0]
        path = os.path.normpath(path.lstrip("/"))
        if path in ("", "."):
            return self.serve_dir
        return os.path.join(self.serve_dir, path)

    def _serve_file(self, path, start=0, end=None, status=200, content_range=None):
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(path)
        if end is None or end >= file_size:
            end = file_size - 1
        length = end - start + 1

        try:
            self.send_response(status)
            ctype = self.guess_type(path)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Last-Modified", self.date_time_string(os.path.getmtime(path)))
            if content_range:
                self.send_header("Content-Range", content_range)
            self.end_headers()
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except ConnectionAbortedError:
            pass
        finally:
            f.close()

    def do_GET(self):
        path = self.translate_path(self.path)

        if os.path.isdir(path):
            self.send_error(404, "File not found")
            return

        if not os.path.isfile(path):
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(path)
        range_header = self.headers.get("Range")

        if range_header:
            import re
            match = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if not match:
                self.send_error(400, "Bad Range header")
                return
            start = int(match.group(1))
            end_str = match.group(2)
            end = int(end_str) if end_str else file_size - 1

            if start >= file_size or start > end:
                self.send_error(416, "Range Not Satisfiable")
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return

            content_range = f"bytes {start}-{end}/{file_size}"
            self._serve_file(path, start, end, 206, content_range)
        else:
            self._serve_file(path, 0, file_size - 1, 200)


class _SilentHandler(_BaseFileHandler):
    pass


class _SlowRangeHandler(_BaseFileHandler):
    def do_GET(self):
        time.sleep(0.3)
        super().do_GET()


class _NoRangeHandler(_BaseFileHandler):
    def do_GET(self):
        self.send_response(200)
        path = self.translate_path(self.path)
        if os.path.isfile(path):
            file_size = os.path.getsize(path)
            ctype = self.guess_type(path)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "none")
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
        else:
            self.send_error(404, "File not found")


class _HTMLHandler(_BaseFileHandler):
    def do_GET(self):
        body = b"<html><body>Access denied</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _BadRequestRangeHandler(_BaseFileHandler):
    def do_GET(self):
        range_header = self.headers.get("Range")
        if range_header:
            self.send_response(416)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            super().do_GET()


def _make_server(port, handler_cls, serve_dir):
    serve_dir_str = str(serve_dir)
    original_chdir = os.getcwd()
    os.chdir(serve_dir)

    class _Handler(handler_cls):
        serve_dir = serve_dir_str

    server = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, original_chdir


@pytest.fixture(scope="module")
def test_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve")
    test_file = serve_dir / "test_download.bin"
    test_data = b"VANTA test download " * 250000
    test_file.write_bytes(test_data)

    server, _original_chdir = _make_server(18345, _SilentHandler, serve_dir)

    yield {
        "url": "http://127.0.0.1:18345/test_download.bin",
        "file_size": len(test_data),
        "file_path": test_file,
    }

    server.shutdown()
    os.chdir(_original_chdir)


@pytest.fixture(scope="module")
def slow_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve_slow")
    test_file = serve_dir / "test_download.bin"
    test_data = b"VANTA test download " * 250000
    test_file.write_bytes(test_data)

    server, _original_chdir = _make_server(18346, _SlowRangeHandler, serve_dir)

    yield {
        "url": "http://127.0.0.1:18346/test_download.bin",
        "file_size": len(test_data),
        "file_path": test_file,
    }

    server.shutdown()
    os.chdir(_original_chdir)


@pytest.fixture(scope="module")
def no_range_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve_norange")
    test_file = serve_dir / "test_download.bin"
    test_data = b"VANTA test download " * 250000
    test_file.write_bytes(test_data)

    server, _original_chdir = _make_server(18347, _NoRangeHandler, serve_dir)

    yield {
        "url": "http://127.0.0.1:18347/test_download.bin",
        "file_size": len(test_data),
        "file_path": test_file,
    }

    server.shutdown()
    os.chdir(_original_chdir)


@pytest.fixture(scope="module")
def html_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve_html")

    server, _original_chdir = _make_server(18348, _HTMLHandler, serve_dir)

    yield {
        "url": "http://127.0.0.1:18348/anything.html",
    }

    server.shutdown()
    os.chdir(_original_chdir)


@pytest.fixture(scope="module")
def bad_range_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve_badrange")
    test_file = serve_dir / "test_download.bin"
    test_data = b"VANTA test download " * 250000
    test_file.write_bytes(test_data)

    server, _original_chdir = _make_server(18349, _BadRequestRangeHandler, serve_dir)

    yield {
        "url": "http://127.0.0.1:18349/test_download.bin",
        "file_size": len(test_data),
        "file_path": test_file,
    }

    server.shutdown()
    os.chdir(_original_chdir)


@pytest.fixture
def download_manager(tmp_path):
    dm = DownloadManager(max_concurrent=3)
    return dm


async def _wait_for_status(task, status, timeout=10.0):
    interval = 0.05
    elapsed = 0.0
    while elapsed < timeout:
        if task.status == status:
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.mark.asyncio
async def test_download_small_file(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "test_download.bin"

    task = await download_manager.add_download(
        name="test_download.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == test_server["file_size"]
    assert task.progress == 100.0
    assert task.total_size == test_server["file_size"]
    assert dest.read_bytes() == test_server["file_path"].read_bytes()

    part_path = dest.with_suffix(dest.suffix + ".part")
    assert not part_path.exists()


@pytest.mark.asyncio
async def test_part_file_created_during_download(slow_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "part_test.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")

    task = await download_manager.add_download(
        name="part_test.bin",
        source_url=slow_server["url"],
        download_url=slow_server["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    while task.status == TaskStatus.PREPARING:
        await asyncio.sleep(0.01)

    assert task.status == TaskStatus.DOWNLOADING
    download_manager.pause_download(task)

    assert await _wait_for_status(task, TaskStatus.PAUSED)
    assert task.status == TaskStatus.PAUSED
    assert part_path.exists()

    download_manager.resume_download(task)

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == slow_server["file_size"]
    assert not part_path.exists()
    assert dest.read_bytes() == slow_server["file_path"].read_bytes()


@pytest.mark.asyncio
async def test_download_progress_updates(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "progress_test.bin"

    task = await download_manager.add_download(
        name="progress_test.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    received = []

    def on_progress(t):
        received.append(t)

    download_manager.add_progress_callback(on_progress)

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    assert await _wait_for_status(task, TaskStatus.COMPLETED)

    download_manager.remove_progress_callback(on_progress)

    assert len(received) > 1
    assert received[-1].downloaded_size == test_server["file_size"]


@pytest.mark.asyncio
async def test_download_cancellation(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "cancel_test.bin"

    task = await download_manager.add_download(
        name="cancel_test.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    assert task.status in (TaskStatus.PREPARING, TaskStatus.DOWNLOADING)

    download_manager.cancel_download(task)

    assert await _wait_for_status(task, TaskStatus.CANCELLED)
    assert task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_download_pause_resume(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "pause_test.bin"

    task = await download_manager.add_download(
        name="pause_test.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    download_manager.pause_download(task)

    assert await _wait_for_status(task, TaskStatus.PAUSED)
    assert task.status == TaskStatus.PAUSED

    download_manager.resume_download(task)
    assert task.status == TaskStatus.QUEUED

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert dest.stat().st_size == test_server["file_size"]


@pytest.mark.asyncio
async def test_resume_with_range_support(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "resume_test.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")

    partial_data = b"VANTA test download " * 10
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(partial_data)

    task = await download_manager.add_download(
        name="resume_test.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert task.supports_resume is True
    assert dest.exists()
    assert dest.stat().st_size == test_server["file_size"]
    assert dest.read_bytes() == test_server["file_path"].read_bytes()
    assert not part_path.exists()


@pytest.mark.asyncio
async def test_416_range_not_satisfiable_recovery(
    bad_range_server, download_manager, tmp_path
):
    dest = tmp_path / "downloads" / "range416_test.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")

    partial_data = b"VANTA test download " * 10
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(partial_data)

    task = await download_manager.add_download(
        name="range416_test.bin",
        source_url=bad_range_server["url"],
        download_url=bad_range_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == bad_range_server["file_size"]
    assert not part_path.exists()
    assert dest.read_bytes() == bad_range_server["file_path"].read_bytes()


@pytest.mark.asyncio
async def test_no_range_support_restarts_from_zero(
    no_range_server, download_manager, tmp_path
):
    dest = tmp_path / "downloads" / "norange_test.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")

    partial_data = b"VANTA test download " * 10
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(partial_data)

    task = await download_manager.add_download(
        name="norange_test.bin",
        source_url=no_range_server["url"],
        download_url=no_range_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert task.supports_resume is False
    assert dest.exists()
    assert dest.stat().st_size == no_range_server["file_size"]
    assert dest.read_bytes() == no_range_server["file_path"].read_bytes()


@pytest.mark.asyncio
async def test_html_response_rejected(html_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "html_reject_test.bin"

    task = await download_manager.add_download(
        name="html_reject_test.bin",
        source_url=html_server["url"],
        download_url=html_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.FAILED, timeout=10.0)
    assert task.status == TaskStatus.FAILED
    assert task.error is not None
    assert "html" in task.error.lower()
    assert not dest.exists()

    part_path = dest.with_suffix(dest.suffix + ".part")
    assert not part_path.exists()


@pytest.mark.asyncio
async def test_resume_download_state_transitions(
    download_manager, test_server, tmp_path
):
    dest = tmp_path / "downloads" / "transition_test.bin"

    task = await download_manager.add_download(
        name="transition_test.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    download_manager.pause_download(task)

    assert await _wait_for_status(task, TaskStatus.PAUSED)
    assert task.status == TaskStatus.PAUSED

    resume_states = []
    download_manager.add_progress_callback(lambda t: resume_states.append(t.status))

    download_manager.resume_download(task)

    assert task.status == TaskStatus.QUEUED

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert TaskStatus.QUEUED in resume_states
    assert TaskStatus.PREPARING in resume_states
    assert TaskStatus.DOWNLOADING in resume_states
    assert TaskStatus.VERIFYING in resume_states


@pytest.mark.asyncio
async def test_range_unsupported_error_type(no_range_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "norange_error_test.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")

    partial_data = b"VANTA test download " * 10
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(partial_data)

    task = await download_manager.add_download(
        name="norange_error_test.bin",
        source_url=no_range_server["url"],
        download_url=no_range_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert task.error_type == DownloadErrorType.RANGE_UNSUPPORTED
    assert dest.exists()
    assert dest.stat().st_size == no_range_server["file_size"]


@pytest.mark.asyncio
async def test_disk_error_classification(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "disk_error_test.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")
    part_path.parent.mkdir(parents=True, exist_ok=True)

    task = await download_manager.add_download(
        name="disk_error_test.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    original_open = builtins.open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path) == str(part_path) and "b" in mode:
            raise OSError("Simulated disk error")
        return original_open(path, mode, *args, **kwargs)

    with unittest.mock.patch("builtins.open", side_effect=failing_open):
        assert await _wait_for_status(task, TaskStatus.FAILED, timeout=10.0)

    assert task.status == TaskStatus.FAILED
    assert task.error_type == DownloadErrorType.DISK


@pytest.mark.asyncio
async def test_speed_limit_zero_is_unlimited(download_manager):
    download_manager.set_speed_limit(0)
    assert download_manager._speed_limit_bytes_per_sec == 0


@pytest.mark.asyncio
async def test_speed_limit_negative_becomes_zero(download_manager):
    download_manager.set_speed_limit(-100)
    assert download_manager._speed_limit_bytes_per_sec == 0


@pytest.mark.asyncio
async def test_speed_limit_throttles_download(tmp_path):
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    import threading

    serve_dir = tmp_path / "serve"
    serve_dir.mkdir()
    test_data = b"X" * 50_000
    test_file = serve_dir / "throttle.bin"
    test_file.write_bytes(test_data)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def translate_path(self, path):
            import urllib.parse
            path = urllib.parse.unquote(path)
            path = path.split("?", 1)[0].split("#", 1)[0]
            return os.path.join(str(serve_dir), path.lstrip("/"))

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/throttle.bin"

    try:
        dm = DownloadManager(max_concurrent=1)
        dm.set_speed_limit(10_000)

        dest = tmp_path / "throttle_download.bin"
        task = await dm.add_download(
            name="throttle.bin",
            source_url=url,
            download_url=url,
            destination=str(dest),
        )

        start = time.monotonic()
        assert await _wait_for_status(task, TaskStatus.COMPLETED, timeout=30.0)
        elapsed = time.monotonic() - start

        assert task.status == TaskStatus.COMPLETED
        assert dest.exists()
        assert dest.stat().st_size == 50_000
        assert elapsed >= 4.0
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_speed_limit_change_during_download(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "dynamic_limit.bin"

    task = await download_manager.add_download(
        name="dynamic_limit.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    download_manager.set_speed_limit(1024 * 1024)

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == test_server["file_size"]


@pytest.mark.asyncio
async def test_speed_limit_does_not_break_resume(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "resume_limit.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")

    partial_data = b"VANTA test download " * 10
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(partial_data)

    download_manager.set_speed_limit(512 * 1024)

    task = await download_manager.add_download(
        name="resume_limit.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    assert await _wait_for_status(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == test_server["file_size"]
    assert dest.read_bytes() == test_server["file_path"].read_bytes()
