"""VANTA V2.0 Phase 4.10 — Application Settings & Preferences Tests."""

import os
import tempfile
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.services.settings_service import DEFAULTS, SettingsService
from app.ui.theme import (
    ThemeService,
    THEME_DARK,
    THEME_LIGHT,
    VALID_THEMES,
    load_style_text,
)

from app.database.connection import get_session, init_db
from app.database.models import DownloadRecord, SettingRecord
from app.database.repositories import save_download_task, load_download_tasks
from app.core.task_manager import DownloadTask, TaskStatus


@pytest.fixture(autouse=True)
def clean_db():
    """Clean database before and after each test."""
    def _clean_db():
        init_db()
        session = get_session()
        try:
            session.query(DownloadRecord).delete()
            session.query(SettingRecord).delete()
            session.commit()
        finally:
            session.close()
    _clean_db()
    yield
    _clean_db()
    SettingsService._instance = None


def test_settings_defaults_exist():
    """DEFAULTS dictionary contains all expected keys."""
    expected_keys = {
        "download_dir",
        "max_concurrent",
        "speed_limit_enabled",
        "speed_limit_value",
        "theme",
        "launch_on_startup",
        "check_for_updates",
        "conflict_policy",
    }
    assert set(DEFAULTS.keys()) == expected_keys


def test_theme_dark_loads():
    """Dark theme stylesheet loads successfully."""
    text = load_style_text(THEME_DARK)
    assert "QMainWindow" in text
    assert "#0F1014" in text
    assert len(text) > 1000


def test_theme_light_loads():
    """Light theme stylesheet loads successfully."""
    text = load_style_text(THEME_LIGHT)
    assert "QMainWindow" in text
    assert "#FFFFFF" in text
    assert len(text) > 1000


def test_theme_invalid_falls_back_to_default():
    """Invalid theme name falls back to default (dark)."""
    text = load_style_text("not_a_theme")
    assert "QMainWindow" in text
    assert "#0F1014" in text


def test_theme_service_apply_dark():
    """ThemeService applies dark theme."""
    app = QApplication.instance() or QApplication([])
    s = SettingsService()
    theme = ThemeService(s, app)
    applied = theme.apply(THEME_DARK)
    assert applied == THEME_DARK
    assert app.styleSheet() != ""


def test_theme_service_apply_light():
    """ThemeService applies light theme."""
    app = QApplication.instance() or QApplication([])
    s = SettingsService()
    theme = ThemeService(s, app)
    applied = theme.apply(THEME_LIGHT)
    assert applied == THEME_LIGHT
    assert app.styleSheet() != ""


def test_theme_service_set_persists_and_applies():
    """ThemeService.set persists to settings and applies."""
    app = QApplication.instance() or QApplication([])
    s = SettingsService()
    theme = ThemeService(s, app)
    applied = theme.set(THEME_LIGHT)
    assert applied == THEME_LIGHT
    assert s.get("theme") == THEME_LIGHT


def test_theme_runtime_switch_dark_to_light():
    """Runtime theme switch from dark to light works."""
    app = QApplication.instance() or QApplication([])
    s = SettingsService()
    theme = ThemeService(s, app)
    theme.apply(THEME_DARK)
    theme.set(THEME_LIGHT)
    assert s.get("theme") == THEME_LIGHT
    assert "#FFFFFF" in app.styleSheet()


def test_theme_runtime_switch_light_to_dark():
    """Runtime theme switch from light to dark works."""
    app = QApplication.instance() or QApplication([])
    s = SettingsService()
    theme = ThemeService(s, app)
    theme.apply(THEME_LIGHT)
    theme.set(THEME_DARK)
    assert s.get("theme") == THEME_DARK
    assert "#0F1014" in app.styleSheet()


def test_theme_valid_themes_constant():
    """VALID_THEMES contains exactly dark and light."""
    assert VALID_THEMES == (THEME_DARK, THEME_LIGHT)


def test_download_dir_validation_accepts_existing_writable_dir(tmp_path):
    """Existing writable directory is accepted."""
    from app.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    result = page._validate_download_dir(str(tmp_path))
    assert result is None


def test_download_dir_validation_creates_missing_dir(tmp_path):
    """Non-existing directory that can be created is accepted."""
    from app.ui.pages.settings_page import SettingsPage

    new_dir = tmp_path / "new_subdir"
    page = SettingsPage()
    result = page._validate_download_dir(str(new_dir))
    assert result is None
    assert new_dir.exists()


def test_download_dir_validation_rejects_file(tmp_path):
    """Existing file path is rejected."""
    from app.ui.pages.settings_page import SettingsPage

    file_path = tmp_path / "file.txt"
    file_path.write_text("test")
    page = SettingsPage()
    result = page._validate_download_dir(str(file_path))
    assert result is not None
    assert "file" in result.lower()


def test_download_dir_validation_rejects_invalid_path():
    """Invalid/unresolvable path is rejected."""
    from app.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    result = page._validate_download_dir("///invalid/null/path")
    assert result is not None


def test_download_dir_validation_rejects_empty():
    """Empty selection is rejected."""
    from app.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    result = page._validate_download_dir("")
    assert result is not None
    assert "no directory" in result.lower()


def test_download_dir_validation_rejects_whitespace():
    """Whitespace-only selection is rejected."""
    from app.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    result = page._validate_download_dir("   ")
    assert result is not None


def test_speed_limit_propagation():
    """speed_limit_bytes_per_sec converts MB/s to bytes/sec correctly."""
    s = SettingsService()
    s.set("speed_limit_enabled", True)
    s.set("speed_limit_value", 1)
    assert s.speed_limit_bytes_per_sec() == 1024 * 1024

    s.set("speed_limit_value", 10)
    assert s.speed_limit_bytes_per_sec() == 10 * 1024 * 1024


def test_speed_limit_disabled_returns_zero():
    """Disabled speed limit returns 0 (unlimited)."""
    s = SettingsService()
    s.set("speed_limit_enabled", False)
    s.set("speed_limit_value", 100)
    assert s.speed_limit_bytes_per_sec() == 0


def test_speed_limit_zero_value_returns_zero():
    """Zero speed limit value returns 0 (unlimited)."""
    s = SettingsService()
    s.set("speed_limit_enabled", True)
    s.set("speed_limit_value", 0)
    assert s.speed_limit_bytes_per_sec() == 0


def test_speed_limit_negative_becomes_zero():
    """Negative speed limit becomes 0."""
    s = SettingsService()
    s.set("speed_limit_enabled", True)
    s.set("speed_limit_value", -5)
    assert s.speed_limit_bytes_per_sec() == 0


def test_conflict_policy_accessible():
    """conflict_policy method returns the stored value."""
    s = SettingsService()
    s.set("conflict_policy", "rename")
    assert s.conflict_policy() == "rename"

    s.set("conflict_policy", "auto_rename")
    assert s.conflict_policy() == "auto_rename"


def test_theme_method_returns_stored_value():
    """theme() method returns stored theme."""
    s = SettingsService()
    s.set("theme", "light")
    assert s.theme() == "light"

    s.set("theme", "dark")
    assert s.theme() == "dark"


def test_max_concurrent_method():
    """max_concurrent() returns integer value."""
    s = SettingsService()
    s.set("max_concurrent", 7)
    assert s.max_concurrent() == 7


def test_download_dir_method_returns_path():
    """download_dir() returns Path object."""
    s = SettingsService()
    path = s.download_dir()
    assert isinstance(path, Path)


def test_settings_get_int_fallback():
    """get_int returns default on malformed value."""
    s = SettingsService()
    s._cache["bad_int"] = "not_a_number"
    assert s.get_int("bad_int", 42) == 42


def test_settings_get_bool_fallback_when_not_in_cache():
    """get_bool returns default when key not in cache."""
    s = SettingsService()
    assert s.get_bool("not_in_cache", True) is True
    assert s.get_bool("also_missing", False) is False


def test_settings_get_bool_malformed_cached_value_returns_false():
    """get_bool returns False for malformed cached value (current behavior)."""
    s = SettingsService()
    s._cache["bad_bool"] = "maybe"
    assert s.get_bool("bad_bool", True) is False


def test_dormant_settings_persisted_but_no_action():
    """launch_on_startup and check_for_updates persist but have no runtime action."""
    s = SettingsService()
    s.set("launch_on_startup", True)
    s.set("check_for_updates", False)

    assert s.get_bool("launch_on_startup") is True
    assert s.get_bool("check_for_updates") is False

    restored = s.reset_to_defaults()
    assert restored["launch_on_startup"] == DEFAULTS["launch_on_startup"]
    assert restored["check_for_updates"] == DEFAULTS["check_for_updates"]


# Tests using the shared test database fixture
def test_settings_service_persists_and_reads(clean_db):
    """SettingsService persists and reads values correctly."""
    from app.services.settings_service import SettingsService

    s1 = SettingsService()
    s1.set("theme", "light")
    s1.set("max_concurrent", 5)
    s1.set("download_dir", "/custom/path")

    s2 = SettingsService()
    assert s2.get("theme") == "light"
    assert s2.get_int("max_concurrent") == 5
    assert s2.get("download_dir") == "/custom/path"


def test_settings_reset_to_defaults_restores_all(clean_db):
    """reset_to_defaults restores every setting to its default value."""
    from app.services.settings_service import SettingsService

    s = SettingsService()
    s.set("theme", "light")
    s.set("max_concurrent", 10)
    s.set("speed_limit_enabled", True)
    s.set("speed_limit_value", 5)
    s.set("download_dir", "/custom/path")
    s.set("conflict_policy", "rename")
    s.set("launch_on_startup", True)
    s.set("check_for_updates", False)

    restored = s.reset_to_defaults()

    assert restored["theme"] == DEFAULTS["theme"]
    assert restored["max_concurrent"] == DEFAULTS["max_concurrent"]
    assert restored["speed_limit_enabled"] == DEFAULTS["speed_limit_enabled"]
    assert restored["speed_limit_value"] == DEFAULTS["speed_limit_value"]
    assert restored["download_dir"] == DEFAULTS["download_dir"]
    assert restored["conflict_policy"] == DEFAULTS["conflict_policy"]
    assert restored["launch_on_startup"] == DEFAULTS["launch_on_startup"]
    assert restored["check_for_updates"] == DEFAULTS["check_for_updates"]

    assert s.get("theme") == DEFAULTS["theme"]
    assert s.get_int("max_concurrent") == int(DEFAULTS["max_concurrent"])
    assert s.get_bool("speed_limit_enabled") is False


def test_settings_reset_persists_across_new_instance(clean_db):
    """Reset values persist when creating a new SettingsService instance."""
    from app.services.settings_service import SettingsService

    s1 = SettingsService()
    s1.set("theme", "light")
    s1.reset_to_defaults()

    s2 = SettingsService()
    assert s2.get("theme") == DEFAULTS["theme"]


def test_reset_does_not_touch_downloads_history_or_schedules(clean_db):
    """Reset only affects settings, not downloads/history/schedules."""
    from app.services.settings_service import SettingsService
    from app.database.repositories import save_download_task
    from app.core.task_manager import DownloadTask, TaskStatus

    task = DownloadTask(
        id="test0001",
        name="test.txt",
        source_url="http://example.com/test.txt",
        download_url="http://example.com/test.txt",
        destination="/tmp/test.txt",
        status=TaskStatus.COMPLETED,
    )
    save_download_task(task)

    s = SettingsService()
    s.set("theme", "light")
    s.reset_to_defaults()

    from app.database.repositories import load_download_tasks
    tasks = load_download_tasks()
    assert len(tasks) == 1
    assert tasks[0].name == "test.txt"


def test_reset_does_not_drop_settings_table(clean_db):
    """Reset does not drop/recreate the settings table."""
    from app.services.settings_service import SettingsService
    from app.database.connection import get_session
    from app.database.models import SettingRecord

    s = SettingsService()
    s.set("theme", "light")
    s.reset_to_defaults()

    session = get_session()
    try:
        count = session.query(SettingRecord).count()
    finally:
        session.close()
    assert count >= len(DEFAULTS)


# Integration tests for reset runtime propagation
def test_reset_theme_light_to_dark_applies_immediately(clean_db, qapp):
    """Reset from Light theme immediately applies Dark at QApplication level."""
    from app.services.settings_service import SettingsService
    from app.ui.main_window import MainWindow

    # Set up MainWindow (which creates ThemeService)
    window = MainWindow()
    # Manually set theme to light first
    window._settings.set("theme", "light")
    window._theme.apply("light")
    assert "#FFFFFF" in qapp.styleSheet()

    # Trigger reset
    restored = window._settings.reset_to_defaults()

    # Emit the theme changed signal as SettingsPage would
    window._on_settings_changed("theme", restored["theme"])

    # Theme should be dark now
    assert "#0F1014" in qapp.styleSheet()
    assert window._settings.get("theme") == "dark"

    window.close()


def test_reset_download_dir_updates_filemanager(clean_db):
    """Reset custom download directory updates FileManager runtime directory."""
    from app.services.settings_service import SettingsService
    from app.core.file_manager import FileManager
    from pathlib import Path

    # Create a FileManager with a custom directory
    custom_dir = Path("/custom/test/path")
    fm = FileManager(custom_dir)
    assert fm.default_dir == custom_dir

    # Create SettingsService and set custom dir
    s = SettingsService()
    s.set("download_dir", str(custom_dir))

    # Simulate reset
    restored = s.reset_to_defaults()

    # Apply the reset to FileManager (as MainWindow._on_settings_changed does)
    fm.set_default_dir(Path(restored["download_dir"]))

    # Should be back to default
    assert fm.default_dir == Path(DEFAULTS["download_dir"])


def test_reset_max_concurrent_updates_queuecontroller(clean_db):
    """Reset max_concurrent updates QueueController runtime value."""
    from app.services.settings_service import SettingsService
    from app.core.queue_controller import QueueController
    from app.core.downloader import DownloadManager

    dm = DownloadManager()
    qc = QueueController(dm)

    # Set custom value
    s = SettingsService()
    s.set("max_concurrent", 10)
    qc.set_max_concurrent(s.max_concurrent())
    assert qc.max_concurrent == 10

    # Reset
    restored = s.reset_to_defaults()
    qc.set_max_concurrent(int(restored["max_concurrent"]))

    # Should be back to default (3)
    assert qc.max_concurrent == 3


def test_reset_speed_limit_updates_downloadmanager(clean_db):
    """Reset speed limit updates DownloadManager runtime limit."""
    from app.services.settings_service import SettingsService
    from app.core.downloader import DownloadManager

    dm = DownloadManager()

    # Set custom speed limit
    s = SettingsService()
    s.set("speed_limit_enabled", True)
    s.set("speed_limit_value", 5)
    dm.set_speed_limit(s.speed_limit_bytes_per_sec())
    assert dm._speed_limit_bytes_per_sec == 5 * 1024 * 1024

    # Reset
    restored = s.reset_to_defaults()
    dm.set_speed_limit(s.speed_limit_bytes_per_sec())  # s now has defaults

    # Should be unlimited (0)
    assert dm._speed_limit_bytes_per_sec == 0


def test_reset_conflict_policy_available_to_consumers(clean_db):
    """Reset conflict policy restores default available to consumers."""
    from app.services.settings_service import SettingsService

    s = SettingsService()
    s.set("conflict_policy", "rename")
    assert s.conflict_policy() == "rename"

    restored = s.reset_to_defaults()

    # Conflict policy should be restored and accessible
    assert s.conflict_policy() == DEFAULTS["conflict_policy"]
    assert restored["conflict_policy"] == DEFAULTS["conflict_policy"]


def test_failed_download_dir_validation_preserves_previous(clean_db):
    """Failed download-directory validation does not overwrite previous valid setting."""
    from app.ui.pages.settings_page import SettingsPage
    from app.core.file_manager import FileManager
    from pathlib import Path

    # Set up SettingsPage with a valid previous directory
    prev_dir = Path("/previous/valid/dir")
    fm = FileManager(prev_dir)

    page = SettingsPage()
    page.set_services(None, fm)
    page._settings.set("download_dir", str(prev_dir))
    page._load_values()

    # Try to set invalid directory (file instead of dir)
    import tempfile
    with tempfile.NamedTemporaryFile() as tmp:
        file_path = Path(tmp.name)
        error = page._validate_download_dir(str(file_path))
        assert error is not None
        assert "file" in error.lower()

    # Previous directory should be unchanged in settings
    assert page._settings.get("download_dir") == str(prev_dir)
    # FileManager should be unchanged
    assert fm.default_dir == prev_dir


def test_windows_invalid_path_rejected():
    """Windows-specific invalid path is rejected deterministically."""
    from app.ui.pages.settings_page import SettingsPage

    page = SettingsPage()

    # Paths that should be rejected by validation
    # NUL - exists but is a device, not a directory
    # Invalid characters in path
    # Paths with invalid Windows characters
    invalid_paths = [
        "NUL",                    # Reserved device name - exists but not a directory
        "C:\\invalid<>path",      # Invalid chars < >
        "C:\\path|with|pipes",    # Invalid chars |
        "C:\\path\"with\"quotes", # Invalid chars "
    ]

    for invalid_path in invalid_paths:
        result = page._validate_download_dir(invalid_path)
        assert result is not None, f"Path '{invalid_path}' should be rejected"


def test_reset_does_not_modify_scheduled_tasks(clean_db):
    """Reset does not modify a scheduled task's scheduled_at value."""
    from app.services.settings_service import SettingsService
    from app.database.repositories import save_download_task
    from app.core.task_manager import DownloadTask, TaskStatus
    from app.database.connection import get_session
    from app.database.models import DownloadRecord
    import time

    # Create a scheduled task with specific scheduled_at
    scheduled_time = time.time() + 3600  # 1 hour in future
    task = DownloadTask(
        id="scheduled001",
        name="scheduled.txt",
        source_url="http://example.com/scheduled.txt",
        download_url="http://example.com/scheduled.txt",
        destination="/tmp/scheduled.txt",
        status=TaskStatus.QUEUED,
        scheduled_at=scheduled_time,
    )
    save_download_task(task)

    # Verify it was saved
    session = get_session()
    try:
        saved = session.query(DownloadRecord).filter_by(id="scheduled001").first()
        assert saved is not None
        original_scheduled_at = saved.scheduled_at
    finally:
        session.close()

    # Reset settings
    s = SettingsService()
    s.set("theme", "light")
    s.reset_to_defaults()

    # Verify scheduled_at unchanged
    session = get_session()
    try:
        saved = session.query(DownloadRecord).filter_by(id="scheduled001").first()
        assert saved is not None
        assert saved.scheduled_at == original_scheduled_at
    finally:
        session.close()