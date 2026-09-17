"""V2.0 Phase 4.8 — Per-Download Bandwidth Management.

Scope (hardening, NOT redesign):
    Existing per-download speed limit
        -> Runtime adjustable (no restart)
        -> Persisted setting (no schema changes)
        -> Clear "per active download" UI semantics
        -> Regression coverage across runtime/lifecycle/progress/security

The speed limit caps EACH active download individually inside
``DownloadManager._stream_to_file``. It is NOT an application-wide aggregate.
Concurrent downloads each enforce the configured value, so aggregate
throughput may approach ``max_concurrent * limit``.

DO NOT introduce:
    - aggregate bandwidth limiter
    - shared HTTP client / connection-pool redesign
    - scheduler/QueueController redesign
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

import asyncio as _real_asyncio

from app.core.downloader import DownloadManager
from app.core.task_manager import DownloadErrorType, DownloadTask, TaskStatus
from app.services.download_security import RedirectWalkResult
from app.services.settings_service import SettingsService
from app.services.url_security import UrlSecurityDecision
from app.utils.logger import setup_logger

setup_logger()


# ── Deterministic fakes ──────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for an httpx.Response consumed by _stream_to_file."""

    def __init__(self, chunks, *, content_type="application/octet-stream", status_code=200):
        self._chunks = list(chunks)
        total = sum(len(c) for c in self._chunks)
        self.headers = {"content-length": str(total), "content-type": content_type}
        self.status_code = status_code
        self.aiter_calls = []
        self.closed = False

    async def aiter_bytes(self, chunk_size):
        for chunk in self._chunks:
            self.aiter_calls.append(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


class _RecordingSleep:
    """Async sleep that records requested delays and returns immediately."""

    def __init__(self):
        self.calls = []

    async def __call__(self, delay, *args, **kwargs):
        self.calls.append(delay)


class _BlockingSleep:
    """Async sleep that records the delay, then blocks until `event` is set.

    Lets a test freeze a throttled download at a deterministic point so it can
    issue pause/cancel/resume/retry exactly while the throttle path is active.
    """

    def __init__(self):
        self.calls = []
        self.event = _real_asyncio.Event()
        self.event.clear()

    async def __call__(self, delay, *args, **kwargs):
        self.calls.append(delay)
        await self.event.wait()


class _FakeTime:
    """Replaces app.core.downloader.time with controllable monotonic/time."""

    def __init__(self, *, constant=None, step=0.01):
        self._constant = constant
        self._step = step
        self._mono = 0.0
        self._wall = 1_700_000_000.0

    def monotonic(self):
        if self._constant is not None:
            return self._constant
        self._mono += self._step
        return self._mono

    def time(self):
        self._wall += self._step
        return self._wall


class _AsyncioShim:
    """Replaces app.core.downloader.asyncio so only sleep() is faked.

    Every other attribute (create_task, CancelledError, gather, Semaphore)
    delegates to the real asyncio module, so the test's own asyncio.sleep and
    task machinery keep working normally.
    """

    def __init__(self, sleep_fn):
        self._sleep_fn = sleep_fn

    def __getattr__(self, name):
        return getattr(_real_asyncio, name)

    async def sleep(self, delay, result=None, *args, **kwargs):
        return await self._sleep_fn(delay, *args, **kwargs)


def _install_fakes(monkeypatch, sleep_fn, fake_time):
    shim = _AsyncioShim(sleep_fn)
    monkeypatch.setattr("app.core.downloader.asyncio", shim)
    monkeypatch.setattr("app.core.downloader.time", fake_time)
    return shim


def _C(n=256 * 1024, byte=b"x"):
    return byte * n


def _make_task(tmp_path, name="dl.bin", download_url="http://example.com/dl.bin"):
    task = DownloadTask(
        id=download_url + ":" + name,
        name=name,
        source_url=download_url,
        download_url=download_url,
        destination=str(tmp_path / "downloads" / name),
    )
    Path(task.destination).parent.mkdir(parents=True, exist_ok=True)
    return task


def _make_walk(response, final_url="http://example.com/dl.bin"):
    """Return a fake walk_redirects coroutine factory returning `response`."""

    async def fake_walk(*args, **kwargs):
        return RedirectWalkResult(
            final_url=final_url,
            response=response,
            hops=0,
            changed_host=False,
            redirect_chain=[final_url],
        )

    return fake_walk


async def _wait_for_status(task, status, timeout=10.0):
    interval = 0.02
    elapsed = 0.0
    while elapsed < timeout:
        if task.status == status:
            return True
        await _real_asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.fixture
def download_manager():
    return DownloadManager(max_concurrent=3, allow_private_networks=True)


@pytest.fixture(autouse=True)
def clean_db():
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord, SettingRecord

    init_db()
    s = get_session()
    try:
        s.query(DownloadRecord).delete()
        s.query(SettingRecord).delete()
        s.commit()
    finally:
        s.close()
    yield
    s = get_session()
    try:
        s.query(DownloadRecord).delete()
        s.query(SettingRecord).delete()
        s.commit()
    finally:
        s.close()


# ═════════════════════════════════════════════════════════════════════════════
# A. Settings
# ═════════════════════════════════════════════════════════════════════════════


def test_speed_limit_value_persists():
    settings = SettingsService()
    settings.set("speed_limit_enabled", True)
    settings.set("speed_limit_value", 5)
    reloaded = SettingsService()
    assert reloaded.get_bool("speed_limit_enabled") is True
    assert reloaded.get_int("speed_limit_value") == 5


def test_speed_limit_enabled_state_persists():
    settings = SettingsService()
    settings.set("speed_limit_enabled", True)
    assert SettingsService().get_bool("speed_limit_enabled") is True
    settings.set("speed_limit_enabled", False)
    assert SettingsService().get_bool("speed_limit_enabled") is False


def test_speed_limit_conversion_mbps_to_bytes_per_sec():
    settings = SettingsService()
    settings.set("speed_limit_enabled", True)
    settings.set("speed_limit_value", 2)
    assert settings.speed_limit_bytes_per_sec() == 2 * 1024 * 1024


def test_speed_limit_disabled_means_unlimited():
    settings = SettingsService()
    settings.set("speed_limit_enabled", True)
    settings.set("speed_limit_value", 2)
    assert settings.speed_limit_bytes_per_sec() == 2 * 1024 * 1024
    settings.set("speed_limit_enabled", False)
    assert settings.speed_limit_bytes_per_sec() == 0


def test_speed_limit_zero_means_unlimited():
    # Preserve existing contract: zero value => no throttling (unlimited).
    settings = SettingsService()
    settings.set("speed_limit_enabled", True)
    settings.set("speed_limit_value", 0)
    assert settings.speed_limit_bytes_per_sec() == 0


def test_positive_speed_limit_reaches_download_manager():
    settings = SettingsService()
    settings.set("speed_limit_enabled", True)
    settings.set("speed_limit_value", 3)
    expected = settings.speed_limit_bytes_per_sec()
    assert expected == 3 * 1024 * 1024

    dm = DownloadManager(max_concurrent=1)
    dm.set_speed_limit(expected)
    # The per-download throttle reads this attribute on every streamed chunk.
    assert dm._speed_limit_bytes_per_sec == 3 * 1024 * 1024


def test_mainwindow_propagates_per_download_limit(qapp):
    """MainWindow routes SettingsService -> DownloadManager.set_speed_limit."""
    from app.core.app_state import AppState
    from app.ui.main_window import MainWindow

    w = MainWindow()
    dm = w._app_state.download_manager
    try:
        assert dm._speed_limit_bytes_per_sec == 0  # default disabled => unlimited

        # enable + 2 MB/s
        w._settings.set("speed_limit_enabled", True)
        w._settings.set("speed_limit_value", 2)
        w._on_settings_changed("speed_limit_enabled", "true")
        assert dm._speed_limit_bytes_per_sec == 2 * 1024 * 1024

        # value change while enabled => updated per-download cap
        w._settings.set("speed_limit_value", 5)
        w._on_settings_changed("speed_limit_value", "5")
        assert dm._speed_limit_bytes_per_sec == 5 * 1024 * 1024

        # disable => unlimited
        w._settings.set("speed_limit_enabled", False)
        w._on_settings_changed("speed_limit_enabled", "false")
        assert dm._speed_limit_bytes_per_sec == 0
    finally:
        w.deleteLater()
        # AppState is a singleton that holds the DownloadManager + a running
        # Scheduler (via MainWindow's QueueController). Reset it so the heavy
        # app wiring does not leak across tests.
        AppState._instance = None


# ═════════════════════════════════════════════════════════════════════════════
# B. Runtime updates (no restart / no requeue)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_active_download_sees_updated_speed_limit(download_manager, tmp_path, monkeypatch):
    """Changing the limit while streaming affects subsequent chunk sleeps."""
    c = _C(128 * 1024, b"y")  # smaller than CHUNK_SIZE to allow multiple chunks
    response = _FakeResponse([c, c])
    task = _make_task(tmp_path, "runtime.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    l1 = 256 * 1024
    l2 = 512 * 1024  # faster cap => smaller per-chunk sleep
    download_manager.set_speed_limit(l1)

    recordings = []

    async def changing_sleep(delay):
        recordings.append(delay)
        if len(recordings) == 1:
            download_manager.set_speed_limit(l2)

    _install_fakes(monkeypatch, changing_sleep, _FakeTime(constant=5.0))
    await download_manager._stream_to_file(response, task, part_path, 0)

    assert download_manager._speed_limit_bytes_per_sec == l2
    assert len(recordings) == 2
    assert recordings[0] == pytest.approx(len(c) / l1)
    assert recordings[1] == pytest.approx(len(c) / l2)


@pytest.mark.asyncio
async def test_disabling_limit_during_stream_removes_throttling(
    download_manager, tmp_path, monkeypatch
):
    """Turning the limit off mid-stream stops throttling later chunks."""
    c = _C(128 * 1024, b"z")
    response = _FakeResponse([c, c, c])
    task = _make_task(tmp_path, "disable.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    download_manager.set_speed_limit(256 * 1024)

    recordings = []

    async def disabling_sleep(delay):
        recordings.append(delay)
        if len(recordings) == 1:
            download_manager.set_speed_limit(0)

    _install_fakes(monkeypatch, disabling_sleep, _FakeTime(constant=5.0))
    await download_manager._stream_to_file(response, task, part_path, 0)

    assert download_manager._speed_limit_bytes_per_sec == 0
    # Only the first chunk is throttled; remaining chunks see limit == 0 (no sleep).
    assert recordings == [pytest.approx(len(c) / (256 * 1024))]


@pytest.mark.asyncio
async def test_changing_limit_does_not_restart_task(download_manager, tmp_path, monkeypatch):
    """set_speed_limit must not create a new asyncio task or alter identity."""
    c = _C(128 * 1024, b"a")
    response = _FakeResponse([c, c])
    task = _make_task(tmp_path, "norestart.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    download_manager.set_speed_limit(256 * 1024)

    sleep = _BlockingSleep()
    fake_time = _FakeTime(constant=5.0)
    _install_fakes(monkeypatch, sleep, fake_time)

    monkeypatch.setattr("app.core.downloader.walk_redirects", _make_walk(_FakeResponse([c, c])))

    asyncio_task = await download_manager.start_download(task)
    try:
        assert await _wait_for_status(task, TaskStatus.DOWNLOADING, timeout=5.0)
        assert len(sleep.calls) == 1  # throttle path engaged
        original_id = task.id
        tasks_before = set(download_manager._tasks.keys())

        # Change the limit while the download is active.
        download_manager.set_speed_limit(512 * 1024)

        tasks_after = set(download_manager._tasks.keys())
        assert tasks_after == tasks_before  # no new task registered
        assert task.id == original_id

        # Release the throttle; the second chunk must use the new cap.
        sleep.event.set()
        assert await _wait_for_status(task, TaskStatus.COMPLETED, timeout=5.0)

        assert len(sleep.calls) == 2
        assert sleep.calls[0] == pytest.approx(len(c) / (256 * 1024))
        assert sleep.calls[1] == pytest.approx(len(c) / (512 * 1024))
    finally:
        if not asyncio_task.done():
            asyncio_task.cancel()
            try:
                await asyncio_task
            except BaseException:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# C. Per-download behavior (independent caps, not an aggregate)
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_one_download_receives_configured_cap(download_manager, tmp_path, monkeypatch):
    c = _C(256 * 1024, b"q")
    response = _FakeResponse([c, c, c])
    task = _make_task(tmp_path, "single.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    limit = 256 * 1024  # 1 chunk (256 KB) / 256 KB/s => 1.0 s per chunk
    download_manager.set_speed_limit(limit)

    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))
    await download_manager._stream_to_file(response, task, part_path, 0)

    per_chunk = pytest.approx(len(c) / limit)
    assert sleep.calls == [per_chunk, per_chunk, per_chunk]
    # Each streamed chunk was written exactly once (no bytes lost/duplicated).
    assert sum(len(ch) for ch in response.aiter_calls) == 3 * len(c)


@pytest.mark.asyncio
async def test_two_downloads_each_independently_enforce_own_cap(tmp_path, monkeypatch):
    """Two managers with different per-download caps: each stream throttles to
    its OWN cap (per-download, not a shared pool)."""
    c1 = _C(256 * 1024, b"1")
    c2 = _C(256 * 1024, b"2")
    limit1 = 256 * 1024
    limit2 = 512 * 1024

    dm1 = DownloadManager(max_concurrent=1)
    dm1.set_speed_limit(limit1)
    dm2 = DownloadManager(max_concurrent=1)
    dm2.set_speed_limit(limit2)

    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    t1 = _make_task(tmp_path, "a.bin")
    await dm1._stream_to_file(_FakeResponse([c1, c1]), t1,
                              Path(t1.destination).with_suffix(".bin.part"), 0)
    d1 = sleep.calls[:]

    t2 = _make_task(tmp_path, "b.bin")
    await dm2._stream_to_file(_FakeResponse([c2, c2]), t2,
                              Path(t2.destination).with_suffix(".bin.part"), 0)
    d2 = sleep.calls[len(d1):]

    assert all(x == pytest.approx(len(c1) / limit1) for x in d1)
    assert all(x == pytest.approx(len(c2) / limit2) for x in d2)
    assert d1[0] != d2[0]  # different caps => different per-download throttling


@pytest.mark.asyncio
async def test_aggregate_is_not_the_configured_limit(download_manager, tmp_path, monkeypatch):
    """With 2 concurrent downloads at limit L, each stream is capped at L
    (aggregate ~ 2*L), NOT split to L/2 (which an aggregate limiter would do).
    Verified by inspecting limiter inputs, not wall clock."""
    c = _C(256 * 1024, b"q")
    limit = 512 * 1024  # full per-download cap: 256KB/512KB/s => 0.5s per chunk
    download_manager.set_speed_limit(limit)

    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    async def _stream(dest_name):
        t = _make_task(tmp_path, dest_name)
        await download_manager._stream_to_file(
            _FakeResponse([c, c]), t, Path(t.destination).with_suffix(".bin.part"), 0
        )

    await asyncio.gather(_stream("ag_a.bin"), _stream("ag_b.bin"))

    per_download = len(c) / limit          # 1.0 s per chunk (full cap)
    aggregate_split = len(c) / (limit / 2)  # 2.0 s per chunk (if shared/aggregate)
    assert per_download == pytest.approx(0.5)
    assert aggregate_split == pytest.approx(1.0)
    assert len(sleep.calls) == 4
    for dur in sleep.calls:
        assert dur == pytest.approx(per_download)
        assert dur != pytest.approx(aggregate_split)


# ═════════════════════════════════════════════════════════════════════════════
# D. Lifecycle under throttle
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pause_works_while_throttled(download_manager, tmp_path, monkeypatch):
    c = _C(256 * 1024, b"p")
    task = _make_task(tmp_path, "pause.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")
    download_manager.set_speed_limit(256 * 1024)

    monkeypatch.setattr("app.core.downloader.walk_redirects", _make_walk(_FakeResponse([c])))
    sleep = _BlockingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    asyncio_task = await download_manager.start_download(task)
    try:
        assert await _wait_for_status(task, TaskStatus.DOWNLOADING, timeout=5.0)
        assert len(sleep.calls) == 1  # throttle path engaged

        download_manager.pause_download(task)
        # Let the cancellation propagate through _run's finally so the task is
        # cleaned up from the registry (not left running).
        try:
            await asyncio_task
        except asyncio.CancelledError:
            pass

        assert task.status == TaskStatus.PAUSED
        assert task.id not in download_manager._tasks
        # Partial data written before pause is preserved.
        assert part_path.exists() and part_path.stat().st_size == len(c)
        assert sleep.calls[0] == pytest.approx(len(c) / (256 * 1024))
    finally:
        if not asyncio_task.done():
            asyncio_task.cancel()
            try:
                await asyncio_task
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_resume_works_while_throttled(download_manager, tmp_path, monkeypatch):
    c = _C(256 * 1024, b"r")
    task = _make_task(tmp_path, "resume.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")
    dest_path = Path(task.destination)
    download_manager.set_speed_limit(256 * 1024)
    limit = 256 * 1024

    # First attempt: blocks on throttle.
    monkeypatch.setattr("app.core.downloader.walk_redirects", _make_walk(_FakeResponse([c])))
    sleep = _BlockingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    asyncio_task = await download_manager.start_download(task)
    try:
        assert await _wait_for_status(task, TaskStatus.DOWNLOADING, timeout=5.0)
        assert len(sleep.calls) == 1
        download_manager.pause_download(task)
        try:
            await asyncio_task
        except asyncio.CancelledError:
            pass
        assert task.status == TaskStatus.PAUSED
        assert part_path.exists() and part_path.stat().st_size == len(c)

        # Resume: throttle still active; throttling re-applies on the resumed stream.
        sleep2 = _BlockingSleep()
        _install_fakes(monkeypatch, sleep2, _FakeTime(constant=5.0))
        monkeypatch.setattr(
            "app.core.downloader.walk_redirects", _make_walk(_FakeResponse([c]))
        )

        download_manager.resume_download(task)
        assert await _wait_for_status(task, TaskStatus.DOWNLOADING, timeout=5.0)
        assert len(sleep2.calls) == 1  # throttling re-applied after resume

        sleep2.event.set()
        assert await _wait_for_status(task, TaskStatus.COMPLETED, timeout=5.0)
        assert dest_path.exists()
        assert dest_path.read_bytes() == c
        assert download_manager._speed_limit_bytes_per_sec == limit
    finally:
        if not asyncio_task.done():
            asyncio_task.cancel()
            try:
                await asyncio_task
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_cancel_works_while_throttled(download_manager, tmp_path, monkeypatch):
    c = _C(256 * 1024, b"x")
    task = _make_task(tmp_path, "cancel.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")
    download_manager.set_speed_limit(256 * 1024)

    monkeypatch.setattr("app.core.downloader.walk_redirects", _make_walk(_FakeResponse([c, c])))
    sleep = _BlockingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    asyncio_task = await download_manager.start_download(task)
    try:
        assert await _wait_for_status(task, TaskStatus.DOWNLOADING, timeout=5.0)
        assert len(sleep.calls) == 1
        download_manager.cancel_download(task)
        try:
            await asyncio_task
        except asyncio.CancelledError:
            pass

        assert task.status == TaskStatus.CANCELLED
        assert task.is_terminal
        assert task.id not in download_manager._tasks
    finally:
        if not asyncio_task.done():
            asyncio_task.cancel()
            try:
                await asyncio_task
            except BaseException:
                pass


@pytest.mark.asyncio
async def test_retry_works_while_throttled(download_manager, tmp_path, monkeypatch):
    """A throttled download that fails can be retried and completes with the
    limit still applied to the retry attempt."""
    c = _C(256 * 1024, b"t")
    task = _make_task(tmp_path, "retry.bin", download_url="http://example.com/retry.bin")
    dest_path = Path(task.destination)
    download_manager.set_speed_limit(256 * 1024)
    limit = 256 * 1024

    attempts = {"n": 0}

    async def failing_then_ok(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            # Transient failure (not a RedirectWalkError) => CONNECTION_INTERRUPTED,
            # which is a retryable (transient) error.
            raise ConnectionError("simulated transient failure")
        return RedirectWalkResult(
            final_url="http://example.com/retry.bin",
            response=_FakeResponse([c]),
            hops=1,
            changed_host=False,
            redirect_chain=["http://example.com/retry.bin"],
        )

    monkeypatch.setattr("app.core.downloader.walk_redirects", failing_then_ok)

    sleep = _BlockingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    # add_download registers the task in _download_tasks so retry_task/find_task
    # can locate it (start_download only registers the asyncio task).
    await download_manager.add_download(
        name=task.name,
        source_url=task.source_url,
        download_url=task.download_url,
        destination=task.destination,
        existing_task=task,
    )
    try:
        assert await _wait_for_status(task, TaskStatus.FAILED, timeout=5.0)
        assert task.error_type == DownloadErrorType.CONNECTION_INTERRUPTED

        download_manager.retry_task(task.id)
        sleep.event.set()  # release the throttle on the retry stream
        assert await _wait_for_status(task, TaskStatus.COMPLETED, timeout=5.0)

        assert dest_path.exists()
        assert dest_path.read_bytes() == c
        assert download_manager._speed_limit_bytes_per_sec == limit
        assert len(sleep.calls) == 1  # throttle applied on the retry attempt
        assert sleep.calls[0] == pytest.approx(len(c) / limit)
    finally:
        asyncio_task = download_manager._tasks.get(task.id)
        if asyncio_task and not asyncio_task.done():
            asyncio_task.cancel()
            try:
                await asyncio_task
            except BaseException:
                pass


# ═════════════════════════════════════════════════════════════════════════════
# E. Progress / speed reporting under throttle
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_throttling_does_not_corrupt_byte_counts(download_manager, tmp_path, monkeypatch):
    """The throttle must not lose or duplicate bytes written to disk."""
    chunks = [_C(256 * 1024, b"1"), _C(256 * 1024, b"2"), _C(128 * 1024, b"3")]
    total = sum(len(ch) for ch in chunks)
    response = _FakeResponse(chunks)
    task = _make_task(tmp_path, "bytes.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    download_manager.set_speed_limit(256 * 1024)
    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(step=0.01))

    await download_manager._stream_to_file(response, task, part_path, 0)

    assert task.downloaded_size == total
    assert part_path.read_bytes() == b"".join(chunks)


@pytest.mark.asyncio
async def test_progress_remains_correct_under_throttle(download_manager, tmp_path, monkeypatch):
    chunks = [_C(256 * 1024, b"A")] * 4
    total = sum(len(ch) for ch in chunks)
    response = _FakeResponse(chunks)
    task = _make_task(tmp_path, "progress.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    download_manager.set_speed_limit(256 * 1024)
    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(step=0.01))

    await download_manager._stream_to_file(response, task, part_path, 0)

    assert task.total_size == total
    assert task.downloaded_size == total
    assert task.progress == 100.0


@pytest.mark.asyncio
async def test_speed_reporting_remains_valid_under_throttle(download_manager, tmp_path, monkeypatch):
    chunks = [_C(256 * 1024, b"B")] * 3
    response = _FakeResponse(chunks)
    task = _make_task(tmp_path, "speed.bin")
    part_path = Path(task.destination).with_suffix(".bin.part")

    download_manager.set_speed_limit(256 * 1024)
    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(step=0.01))

    await download_manager._stream_to_file(response, task, part_path, 0)

    assert isinstance(task.speed, float)
    assert task.speed >= 0.0
    # Measured speed is a valid positive number (clock advances under throttle).
    assert task.speed > 0.0


# ═════════════════════════════════════════════════════════════════════════════
# F. Security / redirect path is not bypassed by throttling
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_throttled_stream_still_uses_redirect_security_path(
    download_manager, tmp_path, monkeypatch
):
    """A throttled download still validates the URL and walks redirects through
    the existing security pipeline (no bypass, no follow_redirects=True)."""
    c = _C(256 * 1024, b"s")
    task = _make_task(tmp_path, "sec.bin")
    dest_path = Path(task.destination)
    part_path = dest_path.with_suffix(".bin.part")
    download_manager.set_speed_limit(256 * 1024)

    response = _FakeResponse([c])

    validate_calls = []

    def recording_validate(url, *, allow_private_networks=False, blocked_hosts=()):
        validate_calls.append(url)
        return UrlSecurityDecision(True, "OK")

    # validate_url is imported locally inside _execute_download.
    monkeypatch.setattr("app.services.url_security.validate_url", recording_validate)

    walk_calls = []

    async def recording_walk(*args, **kwargs):
        walk_calls.append(args)
        return RedirectWalkResult(
            final_url=task.download_url,
            response=response,
            hops=0,
            changed_host=False,
            redirect_chain=[task.download_url],
        )

    monkeypatch.setattr("app.core.downloader.walk_redirects", recording_walk)

    sleep = _RecordingSleep()
    _install_fakes(monkeypatch, sleep, _FakeTime(constant=5.0))

    await download_manager._execute_download(task)

    assert task.status == TaskStatus.COMPLETED
    assert dest_path.exists()
    assert dest_path.read_bytes() == c
    assert validate_calls == [task.download_url]  # initial URL validation ran
    assert len(walk_calls) == 1                  # manual redirect walk ran
    assert response.closed                         # the walked response was finalized
    assert len(sleep.calls) == 1                  # throttle engaged on streamed chunks
    assert sleep.calls[0] == pytest.approx(len(c) / (256 * 1024))


@pytest.mark.asyncio
async def test_no_alternate_http_path_for_throttled_download():
    """Static proof: throttled downloads never use follow_redirects=True nor an
    aggregate/global bandwidth limiter. The throttle is per-stream in
    _stream_to_file via the per-DownloadManager speed_limit attribute."""
    src_execute = inspect.getsource(DownloadManager._execute_download)
    src_stream = inspect.getsource(DownloadManager._stream_to_file)
    src_set = inspect.getsource(DownloadManager.set_speed_limit)

    # Manual redirect walking remains the only HTTP path; no auto-follow bypass.
    assert "follow_redirects=False" in src_execute
    assert "follow_redirects=True" not in src_execute
    # The throttle streams the single walked response (no separate fetch path).
    assert "aiter_bytes" in src_stream
    # Speed cap is a per-instance attribute, not a shared/global limiter class.
    assert "self._speed_limit_bytes_per_sec" in src_stream
    assert "self._speed_limit_bytes_per_sec" in src_set

    # No aggregate/global bandwidth constructs were introduced.
    downloader_source = inspect.getsource(DownloadManager)
    for forbidden in (
        "GlobalBandwidthLimiter",
        "TokenBucket",
        "SharedRateLimiter",
        "ApplicationBandwidthManager",
        "follow_redirects=True",
    ):
        assert forbidden not in downloader_source
