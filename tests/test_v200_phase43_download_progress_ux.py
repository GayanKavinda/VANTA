"""V2.0 Phase 4.3 - Download Execution / Progress UX tests.

Validates that the existing queued/download execution lifecycle is accurately
visible to the user through the Downloads UI: status labels, progress bars,
byte counts, speed, ETA, queue position, and completion/failure states.

Tests are network-free: use controlled fake execution with asyncio.Event
synchronization and the existing DownloadService/QueueController/DownloadManager
boundary. UI tests use PySide6 offscreen platform.

Only adds production changes for demonstrated defects.
"""
import asyncio

import pytest
from PySide6.QtTest import QTest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, TaskStatus
from app.services.download_service import DownloadService


def _file(name: str, size: int | None = 1024) -> DownloadFile:
    return DownloadFile(
        name=name,
        url=f"https://example.com/{name}",
        size=size,
        content_type="application/octet-stream",
    )


def _make_env(tmp_path, max_concurrent=2):
    destination = tmp_path / "downloads"
    destination.mkdir()
    manager = DownloadManager(
        max_concurrent=max_concurrent,
        allow_private_networks=True,
        downloads_dir=tmp_path,
    )
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None,
        download_manager=manager,
        queue_controller=controller,
        file_manager=FileManager(destination),
    )
    return manager, controller, service, destination


async def _submit(service, destination, name, size=1024):
    return await service.start_file_download(
        source_url="https://example.com/page",
        file=_file(name, size),
        destination=str(destination),
        filename=name,
    )


def _create_task(task_id: str, name: str, status=TaskStatus.QUEUED,
                 total_size=0, downloaded_size=0, progress=0.0,
                 speed=0.0, queue_position=0, error=None) -> DownloadTask:
    task = DownloadTask(
        id=task_id,
        name=name,
        source_url="https://example.com/page",
        download_url=f"https://example.com/{name}",
        destination=f"/tmp/{name}",
        status=status,
        total_size=total_size,
        downloaded_size=downloaded_size,
        progress=progress,
        speed=speed,
        error=error,
    )
    task.queue_position = queue_position
    return task


# ── DownloadCard rendering tests ─────────────────────────────────────────────

@pytest.fixture
def card_cls():
    from app.ui.widgets.download_card import DownloadCard
    return DownloadCard


class TestDownloadCardStatusRendering:
    """Verify DownloadCard correctly renders task status labels."""

    def test_queued_status_shows_waiting(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.QUEUED, queue_position=1)
        card = card_cls(task)
        QTest.qWait(10)

        status_text = card._status_label.text()
        assert "Queued" in status_text
        assert "Position #1" in status_text

    def test_queued_without_position_shows_generic(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.QUEUED, queue_position=0)
        card = card_cls(task)
        QTest.qWait(10)

        status_text = card._status_label.text()
        assert "Queued" in status_text
        assert "Position" not in status_text

    def test_downloading_status_shows_downloading(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Downloading" in card._status_label.text()

    def test_paused_status_shows_paused(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.PAUSED)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Paused" in card._status_label.text()

    def test_completed_status_shows_completed(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.COMPLETED)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Completed" in card._status_label.text()

    def test_failed_status_shows_error(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.FAILED, error="Network timeout")
        card = card_cls(task)
        QTest.qWait(10)

        assert "Failed" in card._status_label.text()
        assert "Network timeout" in card._error_label.text()
        assert not card._error_label.isHidden()

    def test_cancelled_status_shows_cancelled(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.CANCELLED)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Cancelled" in card._status_label.text()

    def test_error_label_hidden_when_not_failed(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING, error="old error")
        card = card_cls(task)
        QTest.qWait(10)

        assert card._error_label.isHidden()


class TestDownloadCardProgressRendering:
    """Verify DownloadCard correctly renders progress data."""

    def test_progress_bar_reflects_known_size(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=1000, downloaded_size=250, progress=25.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._progress_bar.value() == 25

    def test_size_label_shows_known_size(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=1000, downloaded_size=250, progress=25.0)
        card = card_cls(task)
        QTest.qWait(10)

        size_text = card._size_label.text()
        assert "250" in size_text
        assert "1000" in size_text

    def test_size_label_unknown_shows_downloaded_only(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=0, downloaded_size=512, progress=0.0)
        card = card_cls(task)
        QTest.qWait(10)

        size_text = card._size_label.text()
        assert "512" in size_text
        assert "/" not in size_text

    def test_speed_label_shows_formatted_speed(self, qapp, card_cls):
        from app.core.task_manager import DownloadTask

        task = DownloadTask(
            id="t1", name="file.zip",
            source_url="https://example.com/page",
            download_url="https://example.com/file.zip",
            destination="/tmp/file.zip",
            status=TaskStatus.DOWNLOADING,
            total_size=1000,
            downloaded_size=250,
            progress=25.0,
            speed=1024.0,
        )
        card = card_cls(task)
        QTest.qWait(10)

        assert "1.0" in card._speed_label.text()
        assert "KB/s" in card._speed_label.text()

    def test_eta_shows_when_downloading_with_known_size(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=1000, downloaded_size=250, progress=25.0,
                            speed=100.0)
        card = card_cls(task)
        QTest.qWait(10)

        eta_text = card._eta_label.text()
        assert "ETA" in eta_text
        assert card._eta_label.text().startswith("ETA")
        assert not card._eta_label.isHidden()

    def test_eta_hidden_when_unknown_size(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=0, downloaded_size=512, progress=0.0,
                            speed=100.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._eta_label.isHidden()

    def test_eta_hidden_when_completed(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.COMPLETED,
                            total_size=1000, downloaded_size=1000, progress=100.0,
                            speed=100.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._eta_label.isHidden()

    def test_completed_shows_100_percent(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.COMPLETED,
                            total_size=1000, downloaded_size=1000, progress=100.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._progress_bar.value() == 100


class TestDownloadCardIndeterminateProgress:
    """Verify progress bar behavior for unknown-size downloads."""

    def test_unknown_size_progress_bar_not_100_percent(self, qapp, card_cls):
        """For unknown-size downloads, the progress bar must NOT misleadingly show 100%."""
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=0, downloaded_size=512, progress=0.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._progress_bar.value() < 100

    def test_unknown_size_shows_downloading_label(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=0, downloaded_size=512, progress=0.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Downloading" in card._status_label.text()

    def test_unknown_size_shows_downloaded_bytes(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=0, downloaded_size=2048, progress=0.0)
        card = card_cls(task)
        QTest.qWait(10)

        size_text = card._size_label.text()
        assert "2048" in size_text or "2.0" in size_text


class TestDownloadCardUpdateFromTask:
    """Verify update_from_task propagates state changes correctly."""

    def test_update_progress_through_execution(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=1000, downloaded_size=0, progress=0.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._progress_bar.value() == 0

        task.downloaded_size = 500
        task.progress = 50.0
        task.speed = 100.0
        card.update_from_task(task)
        QTest.qWait(10)

        assert card._progress_bar.value() == 50

    def test_update_from_completed_state(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=1000, downloaded_size=500, progress=50.0)
        card = card_cls(task)
        QTest.qWait(10)

        assert card._progress_bar.value() == 50

        task.status = TaskStatus.COMPLETED
        task.downloaded_size = 1000
        task.progress = 100.0
        card.update_from_task(task)
        QTest.qWait(10)

        assert card._progress_bar.value() == 100
        assert "Completed" in card._status_label.text()
        assert card._eta_label.isHidden()

    def test_update_from_failed_state(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.DOWNLOADING,
                            total_size=1000, downloaded_size=500, progress=50.0)
        card = card_cls(task)
        QTest.qWait(10)

        task.status = TaskStatus.FAILED
        task.error = "Connection lost"
        card.update_from_task(task)
        QTest.qWait(10)

        assert "Failed" in card._status_label.text()
        assert "Connection lost" in card._error_label.text()
        assert not card._error_label.isHidden()

    def test_update_queue_position_through_lifecycle(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.QUEUED, queue_position=2)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Position #2" in card._status_label.text()

        task.queue_position = 1
        card.update_from_task(task)
        QTest.qWait(10)

        assert "Position #1" in card._status_label.text()

    def test_update_from_queued_to_downloading(self, qapp, card_cls):
        task = _create_task("t1", "file.zip", TaskStatus.QUEUED,
                            total_size=1000, queue_position=1)
        card = card_cls(task)
        QTest.qWait(10)

        assert "Queued" in card._status_label.text()

        task.status = TaskStatus.DOWNLOADING
        card.update_from_task(task)
        QTest.qWait(10)

        assert "Downloading" in card._status_label.text()


class TestDownloadCardMultipleCards:
    """Verify multiple cards update independently."""

    def test_multiple_cards_independent_progress(self, qapp, card_cls):
        task_a = _create_task("t1", "a.zip", TaskStatus.DOWNLOADING,
                              total_size=1000, downloaded_size=100, progress=10.0)
        task_b = _create_task("t2", "b.zip", TaskStatus.DOWNLOADING,
                              total_size=2000, downloaded_size=500, progress=25.0)
        card_a = card_cls(task_a)
        card_b = card_cls(task_b)
        QTest.qWait(10)

        assert card_a._progress_bar.value() == 10
        assert card_b._progress_bar.value() == 25

        task_a.downloaded_size = 500
        task_a.progress = 50.0
        card_a.update_from_task(task_a)
        QTest.qWait(10)

        assert card_a._progress_bar.value() == 50
        assert card_b._progress_bar.value() == 25


# ── DownloadsPage integration tests ────────────────────────────────────────────

@pytest.fixture
def downloads_page(qapp):
    from app.ui.pages.downloads_page import DownloadsPage
    page = DownloadsPage()
    page.show()
    QTest.qWait(10)
    return page


class TestDownloadsPageQueueVisibility:
    """Verify queued tasks are visible with queue position."""

    @pytest.mark.asyncio
    async def test_queued_task_shows_queue_position(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        started = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            started.set()
            await asyncio.Event().wait()

        manager._execute_download = execute

        _first = await _submit(service, dest, "first.zip")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        QTest.qWait(10)

        second = await _submit(service, dest, "queued_task.zip")
        await asyncio.sleep(0.05)
        QTest.qWait(20)

        assert second.id in downloads_page._cards
        card = downloads_page._cards[second.id]
        assert "Queued" in card._status_label.text()
        assert "Position #1" in card._status_label.text()

    @pytest.mark.asyncio
    async def test_summary_counts_reflect_state(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        started = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            started.set()
            await asyncio.Event().wait()

        manager._execute_download = execute

        _first = await _submit(service, dest, "first.zip")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        QTest.qWait(10)

        _second = await _submit(service, dest, "second.zip")
        await asyncio.sleep(0.05)
        QTest.qWait(20)

        summary = downloads_page._summary.text()
        assert "1 Active" in summary


class TestDownloadsPageProgressIntegration:
    """Verify progress data flows from core to UI."""

    @pytest.mark.asyncio
    async def test_progress_updates_reach_card(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        release = asyncio.Event()
        started = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            task.total_size = 1000
            task.downloaded_size = 0
            task.progress = 0.0
            task.speed = 50.0
            manager._emit_progress(task)
            started.set()
            await release.wait()
            task.downloaded_size = 1000
            task.progress = 100.0
            task.speed = 100.0
            manager._emit_progress(task)
            manager._set_status(task, TaskStatus.COMPLETED)

        manager._execute_download = execute

        task = await _submit(service, dest, "progress_test.zip")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        QTest.qWait(20)

        card = downloads_page._cards[task.id]
        assert card._progress_bar.value() == 0

        release.set()
        await asyncio.sleep(0.1)
        QTest.qWait(20)

        assert card._progress_bar.value() == 100
        assert "Completed" in card._status_label.text()

    @pytest.mark.asyncio
    async def test_concurrent_tasks_independent_cards(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=2)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        holds: dict[str, asyncio.Event] = {}

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            task.total_size = 1000
            task.downloaded_size = 0
            task.progress = 0.0
            task.speed = 50.0
            holds[task.id] = asyncio.Event()
            manager._emit_progress(task)
            await holds[task.id].wait()
            task.downloaded_size = 500 if task.name == "a.zip" else 250
            task.progress = 50.0 if task.name == "a.zip" else 25.0
            manager._emit_progress(task)

        manager._execute_download = execute

        first = await _submit(service, dest, "a.zip")
        second = await _submit(service, dest, "b.zip")
        for _ in range(100):
            await asyncio.sleep(0)
            if first.status is TaskStatus.DOWNLOADING and second.status is TaskStatus.DOWNLOADING:
                break
        await asyncio.sleep(0.05)
        QTest.qWait(20)

        card_a = downloads_page._cards[first.id]
        card_b = downloads_page._cards[second.id]

        assert card_a._progress_bar.value() == 0
        assert card_b._progress_bar.value() == 0

        holds[first.id].set()
        holds[second.id].set()
        for _ in range(50):
            await asyncio.sleep(0)
            if card_a._progress_bar.value() == 50 and card_b._progress_bar.value() == 25:
                break
        QTest.qWait(20)

        assert card_a._progress_bar.value() == 50
        assert card_b._progress_bar.value() == 25


class TestDownloadsPageQueuePositionUpdates:
    """Verify queue position is displayed and updates."""

    @pytest.mark.asyncio
    async def test_queue_position_updates_after_completion(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        started = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            started.set()
            await asyncio.Event().wait()

        manager._execute_download = execute

        first = await _submit(service, dest, "first.zip")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        QTest.qWait(10)

        second = await _submit(service, dest, "second.zip")
        await asyncio.sleep(0.05)
        QTest.qWait(20)

        card_b = downloads_page._cards[second.id]
        assert "Queued" in card_b._status_label.text()
        assert "Position #1" in card_b._status_label.text()

        service.cancel_task(first.id)
        await asyncio.sleep(0.05)
        QTest.qWait(20)

        assert "Downloading" in card_b._status_label.text()

    @pytest.mark.asyncio
    async def test_queue_position_after_cancel(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        release = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            await release.wait()
            manager._set_status(task, TaskStatus.COMPLETED)

        manager._execute_download = execute

        _first = await _submit(service, dest, "first.zip")
        await asyncio.sleep(0.05)
        QTest.qWait(10)

        second = await _submit(service, dest, "second.zip")
        await asyncio.sleep(0.05)
        QTest.qWait(10)

        third = await _submit(service, dest, "third.zip")
        await asyncio.sleep(0.05)
        QTest.qWait(20)

        card_b = downloads_page._cards[second.id]
        card_c = downloads_page._cards[third.id]

        assert "Position #1" in card_b._status_label.text()
        assert "Position #2" in card_c._status_label.text()

        service.cancel_task(second.id)
        QTest.qWait(20)

        assert "Position #1" in card_c._status_label.text()


class TestDownloadsPageCompletionFailure:
    """Verify completion and failure states render correctly."""

    @pytest.mark.asyncio
    async def test_completed_task_stops_progress(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        release = asyncio.Event()
        started = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            task.total_size = 1000
            task.downloaded_size = 0
            task.progress = 0.0
            task.speed = 100.0
            manager._emit_progress(task)
            started.set()
            await release.wait()
            task.total_size = 1000
            task.downloaded_size = 1000
            task.progress = 100.0
            task.speed = 100.0
            manager._emit_progress(task)
            manager._set_status(task, TaskStatus.COMPLETED)

        manager._execute_download = execute

        task = await _submit(service, dest, "done.zip")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        QTest.qWait(20)

        card = downloads_page._cards[task.id]
        assert card._progress_bar.value() == 0

        release.set()
        await asyncio.sleep(0.1)
        QTest.qWait(20)

        assert card._progress_bar.value() == 100
        assert "Completed" in card._status_label.text()
        assert card._eta_label.isHidden()

    @pytest.mark.asyncio
    async def test_failed_task_shows_error_and_stops_progress(self, qapp, tmp_path, downloads_page):
        manager, controller, service, dest = _make_env(tmp_path, max_concurrent=1)
        downloads_page.set_queue_controller(controller, service)
        QTest.qWait(10)

        release = asyncio.Event()
        started = asyncio.Event()

        async def execute(task):
            manager._set_status(task, TaskStatus.DOWNLOADING)
            task.total_size = 1000
            task.downloaded_size = 250
            task.progress = 25.0
            task.speed = 100.0
            manager._emit_progress(task)
            started.set()
            await release.wait()
            manager._set_status(task, TaskStatus.FAILED, error="Network error")

        manager._execute_download = execute

        task = await _submit(service, dest, "fail.zip")
        await asyncio.wait_for(started.wait(), timeout=1.0)
        QTest.qWait(20)

        card = downloads_page._cards[task.id]
        assert "Downloading" in card._status_label.text()

        release.set()
        await asyncio.sleep(0.1)
        QTest.qWait(20)

        assert "Failed" in card._status_label.text()
        assert not card._error_label.isHidden()
        assert card._eta_label.isHidden()


class TestDownloadTaskProgressFields:
    """Verify DownloadTask progress fields and formatting."""

    def test_eta_none_for_zero_total_size(self):
        task = _create_task("t1", "file.zip", total_size=0, downloaded_size=0, speed=0.0)
        assert task.eta_seconds is None
        assert task.format_eta() == ""

    def test_eta_none_for_zero_speed(self):
        task = _create_task("t1", "file.zip", total_size=1000, downloaded_size=250, speed=0.0)
        assert task.eta_seconds is None

    def test_eta_zero_when_complete(self):
        task = _create_task("t1", "file.zip", total_size=1000, downloaded_size=1000, speed=100.0)
        assert task.eta_seconds == 0.0
        assert task.format_eta() == "0:00"

    def test_eta_calculated_correctly(self):
        task = _create_task("t1", "file.zip", total_size=1000, downloaded_size=250, speed=50.0)
        assert task.eta_seconds == 15.0
        assert task.format_eta() == "0:15"

    def test_format_speed_handles_various_values(self):
        assert DownloadTask._format_speed(500) == "500.0 B/s"
        assert DownloadTask._format_speed(1024) == "1.0 KB/s"
        assert DownloadTask._format_speed(1048576) == "1.0 MB/s"

    def test_update_progress_sets_all_fields(self):
        task = _create_task("t1", "file.zip", total_size=1000)
        task.update_progress(downloaded=500, total=1000, speed=100.0)

        assert task.downloaded_size == 500
        assert task.total_size == 1000
        assert task.progress == 50.0
        assert task.speed == 100.0

    def test_update_progress_unknown_total_keeps_progress(self):
        task = _create_task("t1", "file.zip", total_size=0)
        task.update_progress(downloaded=500, total=0, speed=100.0)

        assert task.downloaded_size == 500
        assert task.total_size == 0
        assert task.progress == 0.0
        assert task.speed == 100.0

    def test_to_dict_serializes_queue_position(self):
        task = _create_task("t1", "file.zip", TaskStatus.QUEUED, queue_position=3)
        d = task.to_dict()
        assert d["queue_position"] == 3
        assert d["status"] == "queued"
