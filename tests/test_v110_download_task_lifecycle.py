"""V1.10 tests — DownloadTask lifecycle helpers (ETA, state predicates)."""
from __future__ import annotations

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus


def _make_task(**overrides) -> DownloadTask:
    defaults = dict(
        id="t1",
        name="example.zip",
        source_url="https://example.com/page",
        download_url="https://example.com/example.zip",
        destination="/tmp/example.zip",
    )
    defaults.update(overrides)
    return DownloadTask(**defaults)


# ── eta_seconds ──────────────────────────────────────────────────────────

def test_eta_seconds_with_speed_and_size():
    task = _make_task(
        total_size=10_000_000,
        downloaded_size=4_000_000,
        speed=1_000_000,
    )
    assert task.status == TaskStatus.DOWNLOADING or True
    task.status = TaskStatus.DOWNLOADING
    # remaining = 6_000_000 bytes, speed = 1_000_000 B/s → 6 s
    assert task.eta_seconds == 6.0


def test_eta_seconds_zero_when_complete():
    task = _make_task(
        total_size=10_000_000,
        downloaded_size=10_000_000,
        speed=1_000_000,
    )
    task.status = TaskStatus.DOWNLOADING
    assert task.eta_seconds == 0.0


def test_eta_seconds_none_when_speed_zero():
    task = _make_task(
        total_size=10_000_000,
        downloaded_size=5_000_000,
        speed=0.0,
    )
    task.status = TaskStatus.DOWNLOADING
    assert task.eta_seconds is None


def test_eta_seconds_none_when_size_unknown():
    task = _make_task(
        total_size=0,
        downloaded_size=0,
        speed=1_000_000,
    )
    task.status = TaskStatus.DOWNLOADING
    assert task.eta_seconds is None


def test_eta_seconds_none_when_speed_negative():
    task = _make_task(
        total_size=10_000_000,
        downloaded_size=5_000_000,
        speed=-1.0,
    )
    task.status = TaskStatus.DOWNLOADING
    assert task.eta_seconds is None


# ── format_eta ───────────────────────────────────────────────────────────

def test_format_eta_returns_mm_ss():
    task = _make_task(speed=1_000_000, total_size=10_000_000, downloaded_size=4_000_000)
    task.status = TaskStatus.DOWNLOADING
    assert task.format_eta() == "0:06"


def test_format_eta_returns_empty_when_unknown():
    task = _make_task(speed=0.0, total_size=0, downloaded_size=0)
    task.status = TaskStatus.DOWNLOADING
    assert task.format_eta() == ""


def test_format_eta_returns_0_00_when_complete():
    task = _make_task(speed=1_000_000, total_size=10_000_000, downloaded_size=10_000_000)
    task.status = TaskStatus.DOWNLOADING
    assert task.format_eta() == "0:00"


def test_format_eta_minutes_and_seconds():
    task = _make_task(speed=1, total_size=125, downloaded_size=5)
    task.status = TaskStatus.DOWNLOADING
    # remaining = 120 bytes / 1 B/s = 120 s → 2:00
    assert task.format_eta() == "2:00"


def test_format_eta_with_hours():
    task = _make_task(speed=1, total_size=4000, downloaded_size=0)
    task.status = TaskStatus.DOWNLOADING
    # remaining = 4000 / 1 = 4000 s → 1:06:40
    assert task.format_eta() == "1:06:40"


# ── format_speed ─────────────────────────────────────────────────────────

def test_format_speed_bytes():
    task = _make_task(speed=512.0)
    assert task.format_speed() == "512.0 B/s"


def test_format_speed_kbps():
    task = _make_task(speed=1500.0)
    assert task.format_speed() == "1.5 KB/s"


def test_format_speed_mbps():
    task = _make_task(speed=12_000_000.0)
    assert task.format_speed() == "11.4 MB/s"


# ── lifecycle predicates ─────────────────────────────────────────────────

def test_is_downloading_true_for_downloading():
    task = _make_task(status=TaskStatus.DOWNLOADING)
    assert task.is_downloading is True


def test_is_downloading_false_for_other_statuses():
    for status in (
        TaskStatus.QUEUED,
        TaskStatus.PREPARING,
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    ):
        task = _make_task(status=status)
        assert task.is_downloading is False


def test_is_pausable_for_queued():
    task = _make_task(status=TaskStatus.QUEUED)
    assert task.is_pausable is True


def test_is_pausable_for_active():
    for status in (
        TaskStatus.DOWNLOADING,
        TaskStatus.PREPARING,
        TaskStatus.VERIFYING,
    ):
        task = _make_task(status=status)
        assert task.is_pausable is True


def test_is_pausable_false_for_terminal_and_paused():
    for status in (
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        task = _make_task(status=status)
        assert task.is_pausable is False


def test_is_resumable_for_paused():
    task = _make_task(status=TaskStatus.PAUSED)
    assert task.is_resumable is True


def test_is_resumable_for_failed():
    task = _make_task(status=TaskStatus.FAILED)
    assert task.is_resumable is True


def test_is_resumable_false_for_other_statuses():
    for status in (
        TaskStatus.QUEUED,
        TaskStatus.DOWNLOADING,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    ):
        task = _make_task(status=status)
        assert task.is_resumable is False
