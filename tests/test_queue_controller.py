import asyncio
import os
import shutil
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.downloader import DownloadManager
from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.repositories import load_download_tasks, save_download_task
from app.utils.logger import setup_logger

setup_logger()


class _BaseFileHandler(SimpleHTTPRequestHandler):
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


class _SlowHandler(_BaseFileHandler):
    def do_GET(self):
        time.sleep(0.2)
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
def slow_server(tmp_path_factory):
    serve_dir = tmp_path_factory.mktemp("serve_slow")
    files = {}
    for name in ("a.bin", "b.bin", "c.bin"):
        data = b"QUEUE TEST " * 10000
        (serve_dir / name).write_bytes(data)
        files[name] = {"url": f"http://127.0.0.1:18360/{name}", "size": len(data), "path": serve_dir / name}

    server, original_chdir = _make_server(18360, _SlowHandler, serve_dir)

    yield files

    server.shutdown()
    os.chdir(original_chdir)


@pytest.fixture
def queue_manager():
    return DownloadManager(max_concurrent=1)


async def _wait(task, status, timeout=10.0):
    interval = 0.05
    elapsed = 0.0
    while elapsed < timeout:
        if task.status == status:
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.mark.asyncio
async def test_queue_positions_assigned_sequentially(queue_manager, slow_server, tmp_path):
    for name in ("a.bin", "b.bin", "c.bin"):
        await queue_manager.add_download(
            name=name,
            source_url=slow_server[name]["url"],
            download_url=slow_server[name]["url"],
            destination=str(tmp_path / name),
        )

    tasks = queue_manager.download_tasks
    assert tasks[0].queue_position == 1
    assert tasks[1].queue_position == 2
    assert tasks[2].queue_position == 3


@pytest.mark.asyncio
async def test_pause_active_download(queue_manager, slow_server, tmp_path):
    task = await queue_manager.add_download(
        name="a.bin",
        source_url=slow_server["a.bin"]["url"],
        download_url=slow_server["a.bin"]["url"],
        destination=str(tmp_path / "a.bin"),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    queue_manager.pause_download(task)
    assert await _wait(task, TaskStatus.PAUSED)
    assert task.status == TaskStatus.PAUSED


@pytest.mark.asyncio
async def test_resume_paused_download(queue_manager, slow_server, tmp_path):
    task = await queue_manager.add_download(
        name="a.bin",
        source_url=slow_server["a.bin"]["url"],
        download_url=slow_server["a.bin"]["url"],
        destination=str(tmp_path / "a.bin"),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    queue_manager.pause_download(task)
    assert await _wait(task, TaskStatus.PAUSED)

    queue_manager.resume_download(task)
    assert task.status == TaskStatus.QUEUED
    assert await _wait(task, TaskStatus.COMPLETED)


@pytest.mark.asyncio
async def test_pause_all_active_and_queued(queue_manager, slow_server, tmp_path):
    for name in ("a.bin", "b.bin"):
        await queue_manager.add_download(
            name=name,
            source_url=slow_server[name]["url"],
            download_url=slow_server[name]["url"],
            destination=str(tmp_path / name),
        )

    active = queue_manager.download_tasks[0]
    queued = queue_manager.download_tasks[1]

    while active.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    queue_manager.pause_all()

    assert active.status == TaskStatus.PAUSED
    assert queued.status == TaskStatus.PAUSED


@pytest.mark.asyncio
async def test_resume_all_restores_queue(queue_manager, slow_server, tmp_path):
    for name in ("a.bin", "b.bin"):
        await queue_manager.add_download(
            name=name,
            source_url=slow_server[name]["url"],
            download_url=slow_server[name]["url"],
            destination=str(tmp_path / name),
        )

    active = queue_manager.download_tasks[0]
    queued = queue_manager.download_tasks[1]

    while active.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    queue_manager.pause_all()
    assert await _wait(active, TaskStatus.PAUSED)
    assert queued.status == TaskStatus.PAUSED

    queue_manager.resume_all()

    assert active.status == TaskStatus.QUEUED
    assert queued.status == TaskStatus.QUEUED
    assert active.queue_position == 1
    assert queued.queue_position == 2


@pytest.mark.asyncio
async def test_cancel_all_active_and_queued(queue_manager, slow_server, tmp_path):
    for name in ("a.bin", "b.bin", "c.bin"):
        await queue_manager.add_download(
            name=name,
            source_url=slow_server[name]["url"],
            download_url=slow_server[name]["url"],
            destination=str(tmp_path / name),
        )

    queue_manager.cancel_all()

    for task in queue_manager.download_tasks:
        assert task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_retry_failed_with_part_file(queue_manager, slow_server, tmp_path):
    dest = tmp_path / "a.bin"
    part = dest.with_suffix(dest.suffix + ".part")
    actual_data = slow_server["a.bin"]["path"].read_bytes()
    partial = actual_data[:800]
    part.write_bytes(partial)

    task = await queue_manager.add_download(
        name="a.bin",
        source_url=slow_server["a.bin"]["url"],
        download_url=slow_server["a.bin"]["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    queue_manager.pause_download(task)
    while task.status != TaskStatus.PAUSED:
        await asyncio.sleep(0.01)

    task.status = TaskStatus.FAILED
    task.error = "Network timeout"
    task.error_type = DownloadErrorType.NETWORK
    task.supports_resume = True
    task.downloaded_size = len(partial)
    task.total_size = slow_server["a.bin"]["size"]
    queue_manager._emit_progress(task)

    queue_manager.retry_failed()

    assert task.status == TaskStatus.QUEUED
    assert task.error is None
    assert part.exists()

    assert await _wait(task, TaskStatus.COMPLETED)
    assert dest.exists()
    assert dest.read_bytes() == slow_server["a.bin"]["path"].read_bytes()


@pytest.mark.asyncio
async def test_retry_failed_without_part_file(queue_manager, slow_server, tmp_path):
    dest = tmp_path / "a.bin"
    part = dest.with_suffix(dest.suffix + ".part")

    task = await queue_manager.add_download(
        name="a.bin",
        source_url=slow_server["a.bin"]["url"],
        download_url=slow_server["a.bin"]["url"],
        destination=str(dest),
    )

    while task.status == TaskStatus.QUEUED:
        await asyncio.sleep(0.01)

    queue_manager.pause_download(task)
    while task.status != TaskStatus.PAUSED:
        await asyncio.sleep(0.01)

    task.status = TaskStatus.FAILED
    task.error = "Access denied"
    task.downloaded_size = 5000
    task.total_size = slow_server["a.bin"]["size"]
    queue_manager._emit_progress(task)

    queue_manager.retry_failed()

    assert task.status == TaskStatus.QUEUED
    assert task.error is None
    assert task.downloaded_size == 0
    assert task.total_size == 0
    assert not part.exists()

    assert await _wait(task, TaskStatus.COMPLETED)
    assert dest.exists()
    assert dest.read_bytes() == slow_server["a.bin"]["path"].read_bytes()


@pytest.mark.asyncio
async def test_clear_completed_removes_from_list(queue_manager, slow_server, tmp_path):
    for name in ("a.bin", "b.bin"):
        await queue_manager.add_download(
            name=name,
            source_url=slow_server[name]["url"],
            download_url=slow_server[name]["url"],
            destination=str(tmp_path / name),
        )

    for task in queue_manager.download_tasks:
        assert await _wait(task, TaskStatus.COMPLETED)

    queue_manager.clear_completed()

    assert len(queue_manager.download_tasks) == 0


@pytest.mark.asyncio
async def test_crash_recovery_queued_downloads(tmp_path):
    task = DownloadTask(
        id=f"crash_queued_{int(time.time()*1000)}",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination=str(tmp_path / "a.bin"),
        status=TaskStatus.QUEUED,
    )
    save_download_task(task)

    try:
        restored = load_download_tasks()
        queued = [t for t in restored if t.status == TaskStatus.QUEUED and t.id == task.id]
        interrupted = [
            t for t in restored
            if t.id == task.id and t.status in (TaskStatus.DOWNLOADING, TaskStatus.PREPARING, TaskStatus.VERIFYING)
        ]

        assert len(queued) == 1
        assert len(interrupted) == 0
    finally:
        from app.database.connection import get_session
        from app.database.models import DownloadRecord
        db = get_session()
        try:
            db.query(DownloadRecord).filter_by(id=task.id).delete()
            db.commit()
        finally:
            db.close()


@pytest.mark.asyncio
async def test_crash_recovery_interrupted_downloads(tmp_path):
    task = DownloadTask(
        id=f"crash_interrupted_{int(time.time()*1000)}",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination=str(tmp_path / "a.bin"),
        status=TaskStatus.DOWNLOADING,
        downloaded_size=5000,
        total_size=100000,
    )
    save_download_task(task)

    try:
        restored = load_download_tasks()
        interrupted = [
            t for t in restored
            if t.id == task.id and t.status in (TaskStatus.DOWNLOADING, TaskStatus.PREPARING, TaskStatus.VERIFYING)
        ]

        assert len(interrupted) == 1
        assert interrupted[0].status == TaskStatus.DOWNLOADING
    finally:
        from app.database.connection import get_session
        from app.database.models import DownloadRecord
        db = get_session()
        try:
            db.query(DownloadRecord).filter_by(id=task.id).delete()
            db.commit()
        finally:
            db.close()


@pytest.mark.asyncio
async def test_sqlite_state_persisted_after_transitions(queue_manager, slow_server, tmp_path):
    task = await queue_manager.add_download(
        name="a.bin",
        source_url=slow_server["a.bin"]["url"],
        download_url=slow_server["a.bin"]["url"],
        destination=str(tmp_path / "a.bin"),
    )

    assert await _wait(task, TaskStatus.COMPLETED)

    save_download_task(task)

    from app.database.connection import get_session
    from app.database.models import DownloadRecord
    db = get_session()
    try:
        model = db.query(DownloadRecord).filter_by(id=task.id).first()
        assert model is not None
        assert model.status == TaskStatus.COMPLETED.value
        assert model.downloaded_size == slow_server["a.bin"]["size"]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_retry_task_only_retries_requested_task(queue_manager):
    a = DownloadTask(
        id="retry_a",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination="/tmp/a.bin",
        status=TaskStatus.FAILED,
    )
    b = DownloadTask(
        id="retry_b",
        name="b.bin",
        source_url="http://example.com/b.bin",
        download_url="http://example.com/b.bin",
        destination="/tmp/b.bin",
        status=TaskStatus.FAILED,
    )
    queue_manager._download_tasks.extend([a, b])

    queue_manager.retry_task("retry_a")

    assert a.status == TaskStatus.QUEUED
    assert b.status == TaskStatus.FAILED


def test_retry_task_ignores_non_failed_task(queue_manager):
    task = DownloadTask(
        id="retry_ok",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination="/tmp/a.bin",
        status=TaskStatus.COMPLETED,
    )
    queue_manager._download_tasks.append(task)

    queue_manager.retry_task("retry_ok")

    assert task.status == TaskStatus.COMPLETED


def test_clear_completed_keeps_failed(queue_manager):
    completed = DownloadTask(
        id="clr_comp",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination="/tmp/a.bin",
        status=TaskStatus.COMPLETED,
    )
    failed = DownloadTask(
        id="clr_fail",
        name="b.bin",
        source_url="http://example.com/b.bin",
        download_url="http://example.com/b.bin",
        destination="/tmp/b.bin",
        status=TaskStatus.FAILED,
    )
    queue_manager._download_tasks.extend([completed, failed])

    queue_manager.clear_completed()

    assert len(queue_manager.download_tasks) == 1
    assert queue_manager.download_tasks[0].id == "clr_fail"


def test_clear_completed_keeps_cancelled(queue_manager):
    completed = DownloadTask(
        id="clr_comp2",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination="/tmp/a.bin",
        status=TaskStatus.COMPLETED,
    )
    cancelled = DownloadTask(
        id="clr_cancel",
        name="b.bin",
        source_url="http://example.com/b.bin",
        download_url="http://example.com/b.bin",
        destination="/tmp/b.bin",
        status=TaskStatus.CANCELLED,
    )
    queue_manager._download_tasks.extend([completed, cancelled])

    queue_manager.clear_completed()

    assert len(queue_manager.download_tasks) == 1
    assert queue_manager.download_tasks[0].id == "clr_cancel"


def test_restore_tasks_converts_interrupted_to_paused(queue_manager):
    downloading = DownloadTask(
        id="restore_down",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination="/tmp/a.bin",
        status=TaskStatus.DOWNLOADING,
        queue_order=2,
    )
    preparing = DownloadTask(
        id="restore_prep",
        name="b.bin",
        source_url="http://example.com/b.bin",
        download_url="http://example.com/b.bin",
        destination="/tmp/b.bin",
        status=TaskStatus.PREPARING,
        queue_order=1,
    )
    verifying = DownloadTask(
        id="restore_verify",
        name="c.bin",
        source_url="http://example.com/c.bin",
        download_url="http://example.com/c.bin",
        destination="/tmp/c.bin",
        status=TaskStatus.VERIFYING,
        queue_order=3,
    )
    paused = DownloadTask(
        id="restore_paused",
        name="d.bin",
        source_url="http://example.com/d.bin",
        download_url="http://example.com/d.bin",
        destination="/tmp/d.bin",
        status=TaskStatus.PAUSED,
        queue_order=0,
    )

    queue_manager.restore_tasks([downloading, preparing, verifying, paused])

    assert downloading.status == TaskStatus.PAUSED
    assert downloading.error == "Download was interrupted"
    assert downloading.error_type == DownloadErrorType.NETWORK
    assert preparing.status == TaskStatus.PAUSED
    assert preparing.error == "Download was interrupted"
    assert verifying.status == TaskStatus.PAUSED
    assert paused.status == TaskStatus.PAUSED


def test_restore_tasks_emits_final_state(queue_manager):
    downloading = DownloadTask(
        id="restore_emit",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination="/tmp/a.bin",
        status=TaskStatus.DOWNLOADING,
        queue_order=1,
    )

    emitted = []
    queue_manager.add_progress_callback(lambda t: emitted.append(t))

    queue_manager.restore_tasks([downloading])

    assert downloading.status == TaskStatus.PAUSED
    assert downloading in emitted

    queue_manager.remove_progress_callback(lambda t: emitted.append(t))


def test_queue_order_persisted(tmp_path):
    from app.database.repositories import save_download_task, load_download_tasks

    a = DownloadTask(
        id="qorder_a",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination=str(tmp_path / "a.bin"),
        status=TaskStatus.QUEUED,
        queue_order=3,
    )
    b = DownloadTask(
        id="qorder_b",
        name="b.bin",
        source_url="http://example.com/b.bin",
        download_url="http://example.com/b.bin",
        destination=str(tmp_path / "b.bin"),
        status=TaskStatus.QUEUED,
        queue_order=1,
    )
    c = DownloadTask(
        id="qorder_c",
        name="c.bin",
        source_url="http://example.com/c.bin",
        download_url="http://example.com/c.bin",
        destination=str(tmp_path / "c.bin"),
        status=TaskStatus.QUEUED,
        queue_order=2,
    )

    for task in [a, b, c]:
        save_download_task(task)

    try:
        loaded = load_download_tasks()
        loaded_map = {t.id: t for t in loaded}

        assert loaded_map["qorder_a"].queue_order == 3
        assert loaded_map["qorder_b"].queue_order == 1
        assert loaded_map["qorder_c"].queue_order == 2
    finally:
        from app.database.connection import get_session
        from app.database.models import DownloadRecord
        db = get_session()
        try:
            db.query(DownloadRecord).filter(DownloadRecord.id.in_(["qorder_a", "qorder_b", "qorder_c"])).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


def test_queue_order_restored(tmp_path):
    from app.database.repositories import save_download_task, load_download_tasks

    a = DownloadTask(
        id="qorder_restore_a",
        name="a.bin",
        source_url="http://example.com/a.bin",
        download_url="http://example.com/a.bin",
        destination=str(tmp_path / "a.bin"),
        status=TaskStatus.QUEUED,
        queue_order=3,
    )
    b = DownloadTask(
        id="qorder_restore_b",
        name="b.bin",
        source_url="http://example.com/b.bin",
        download_url="http://example.com/b.bin",
        destination=str(tmp_path / "b.bin"),
        status=TaskStatus.QUEUED,
        queue_order=1,
    )
    c = DownloadTask(
        id="qorder_restore_c",
        name="c.bin",
        source_url="http://example.com/c.bin",
        download_url="http://example.com/c.bin",
        destination=str(tmp_path / "c.bin"),
        status=TaskStatus.QUEUED,
        queue_order=2,
    )

    for task in [a, b, c]:
        save_download_task(task)

    try:
        loaded = load_download_tasks()
        dm = DownloadManager(max_concurrent=1)
        dm.restore_tasks(loaded)

        queued = [t for t in dm.download_tasks if t.status == TaskStatus.QUEUED]
        assert queued[0].id == "qorder_restore_b"
        assert queued[0].queue_position == 1
        assert queued[1].id == "qorder_restore_c"
        assert queued[1].queue_position == 2
        assert queued[2].id == "qorder_restore_a"
        assert queued[2].queue_position == 3
    finally:
        from app.database.connection import get_session
        from app.database.models import DownloadRecord
        db = get_session()
        try:
            db.query(DownloadRecord).filter(DownloadRecord.id.in_(["qorder_restore_a", "qorder_restore_b", "qorder_restore_c"])).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
