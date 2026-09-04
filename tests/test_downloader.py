import asyncio
import os
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.task_manager import DownloadTask, TaskStatus
from app.utils.constants import CHUNK_SIZE
from app.utils.logger import setup_logger

setup_logger()


class _SilentHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def test_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve")
    test_file = serve_dir / "test_download.bin"
    test_data = b"VANTA test download " * 1000
    test_file.write_bytes(test_data)

    os.chdir(serve_dir)

    server = HTTPServer(("127.0.0.1", 18345), _SilentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield {
        "url": "http://127.0.0.1:18345/test_download.bin",
        "file_size": len(test_data),
        "file_path": test_file,
    }

    server.shutdown()


@pytest.fixture
def download_manager(tmp_path):
    fm = FileManager(tmp_path / "downloads")
    dm = DownloadManager(max_concurrent=3)
    return dm


@pytest.mark.asyncio
async def test_download_small_file(test_server, download_manager, tmp_path):
    dest = tmp_path / "downloads" / "test_download.bin"

    task = await download_manager.add_download(
        name="test_download.bin",
        source_url=test_server["url"],
        download_url=test_server["url"],
        destination=str(dest),
    )

    for _ in range(200):
        if task.status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)

    assert task.status == TaskStatus.COMPLETED
    assert dest.exists()
    assert dest.stat().st_size == test_server["file_size"]
    assert task.progress == 100.0
    assert task.total_size == test_server["file_size"]


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

    for _ in range(200):
        if task.status == TaskStatus.COMPLETED:
            break
        await asyncio.sleep(0.05)

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

    for _ in range(50):
        if task.status == TaskStatus.CANCELLED:
            break
        await asyncio.sleep(0.05)

    assert task.status == TaskStatus.CANCELLED
