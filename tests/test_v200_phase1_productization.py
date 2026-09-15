"""V2.0 Phase 1 — Productization Foundation tests.

These tests verify the V2.0 productization work without touching
any frozen V1.x architecture.
"""

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from app.metadata import (
    APP_BUILD,
    APP_DESCRIPTION,
    APP_HOMEPAGE,
    APP_LICENSE,
    APP_NAME,
    APP_VENDOR,
    APP_VERSION,
    APP_VERSION_DISPLAY,
    APP_VERSION_TUPLE,
    about_text,
    app_version_string,
)
from app.version import (
    VERSION,
    VERSION_MAJOR,
    VERSION_MINOR,
    VERSION_PATCH,
    VERSION_SUFFIX,
)


# ── Authoritative version ───────────────────────────────────────────────────

def test_version_is_single_source_of_truth():
    """The version string lives only in app.version."""
    assert VERSION == "2.0.0-dev"
    assert VERSION_SUFFIX == "dev"
    assert (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH) == (2, 0, 0)


def test_metadata_imports_version():
    """app.metadata re-exports the authoritative version."""
    assert APP_VERSION == VERSION
    assert APP_VERSION_DISPLAY == "2.0.0"
    assert APP_VERSION_TUPLE == (2, 0, 0)


def test_no_hardcoded_version_strings_elsewhere():
    """The literal version string should not appear in source files.

    Only app/version.py and app/metadata.py may contain the literal
    '2.0.0-dev'.  main.py and constants.py must import it.
    """
    import inspect
    from app import version as version_module
    from app import metadata as metadata_module
    from app.utils import constants as constants_module
    import main as main_module

    # These modules must not hard-code the version string.
    for mod in (constants_module, main_module):
        source = inspect.getsource(mod)
        assert "2.0.0-dev" not in source, (
            f"Hard-coded version string found in {mod.__name__}"
        )


# ── Application metadata ────────────────────────────────────────────────────

def test_metadata_fields():
    """Required metadata fields are populated."""
    assert APP_NAME == "VANTA"
    assert APP_VENDOR  # non-empty
    assert APP_DESCRIPTION  # non-empty
    assert APP_HOMEPAGE.startswith("http")
    assert APP_LICENSE  # non-empty
    assert APP_BUILD == "dev"


def test_app_version_string_format():
    """app_version_string() returns a user-facing string."""
    s = app_version_string()
    assert "VANTA" in s
    assert "2.0.0" in s
    assert "dev" in s


def test_about_text_contains_key_info():
    """about_text() includes name, version, and vendor."""
    text = about_text()
    assert "VANTA" in text
    assert "2.0.0" in text
    assert APP_VENDOR in text


# ── Production paths ────────────────────────────────────────────────────────

def test_runtime_paths_under_localappdata():
    """Database and logs live under %LOCALAPPDATA%\\VANTA when available."""
    from app.utils.constants import DATA_DIR, LOGS_DIR, DB_PATH, LOG_PATH

    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        expected_parent = Path(localappdata) / APP_NAME
        assert expected_parent in DB_PATH.parents
        assert expected_parent in LOGS_DIR.parents
        assert DB_PATH == DATA_DIR / "vanta.db"
        assert LOG_PATH == LOGS_DIR / "vanta.log"


def test_runtime_paths_fallback_when_localappdata_missing():
    """When LOCALAPPDATA is unset, paths fall back to BASE_DIR."""
    from app.utils import constants as constants_module

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LOCALAPPDATA", None)
        # Re-import to re-evaluate paths
        import importlib
        importlib.reload(constants_module)

        try:
            assert constants_module.DATA_DIR.parent == (
                constants_module.BASE_DIR / APP_NAME
            )
            assert constants_module.LOGS_DIR.parent == (
                constants_module.BASE_DIR / APP_NAME
            )
        finally:
            # Restore for subsequent tests
            importlib.reload(constants_module)


# ── Clean-start behaviour ───────────────────────────────────────────────────

def test_ensure_runtime_dirs_creates_directories(tmp_path):
    """_ensure_runtime_dirs() creates DATA_DIR and LOGS_DIR."""
    from app.utils import constants as constants_module

    fake_root = tmp_path / "VANTA"
    fake_data = fake_root / "data"
    fake_logs = fake_root / "logs"

    with mock.patch.object(constants_module, "DATA_DIR", fake_data), \
         mock.patch.object(constants_module, "LOGS_DIR", fake_logs):
        # Import the function fresh so it picks up the patched constants
        import importlib
        import main as main_module
        importlib.reload(main_module)
        main_module._ensure_runtime_dirs()

        assert fake_data.exists()
        assert fake_logs.exists()


# ── Version visibility in settings UI ───────────────────────────────────────

def test_settings_page_shows_version():
    """SettingsPage includes an About section with the version string."""
    from PySide6.QtWidgets import QLabel
    from app.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    # Find QLabel widgets that contain the version info
    found = False
    for label in page.findChildren(QLabel):
        text = label.text()
        if "VANTA" in text and "2.0.0" in text:
            found = True
            break
    assert found, "SettingsPage should display the VANTA version"


# ── No development-only runtime dependencies ────────────────────────────────

def test_no_dev_only_path_hardcoding():
    """Runtime code must not hard-code repository-relative paths."""
    import inspect
    import main as main_module
    from app.utils import constants as constants_module

    # main.py should use constants, not hard-coded relative paths
    main_source = inspect.getsource(main_module)
    assert "BASE_DIR" not in main_source or "assets" in main_source  # style path is OK
    # constants.py should not reference the repo dir for runtime paths
    const_source = inspect.getsource(constants_module)
    assert "wamp64" not in const_source
    assert "C:\\" not in const_source
    assert "C:/" not in const_source