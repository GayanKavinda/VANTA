"""VANTA V2.0 Phase 4.11 (M1) — Windows Desktop Integration: Branding & Packaging.

Scope-limited, read/no-tray, offscreen-safe tests covering only the Phase 4.11
M1 deliverables: application icon, Qt window/application icon, PyInstaller icon,
Windows version resource, and the Per-Monitor DPI-awareness manifest.

Phase 4.9 (shutdown) and Phase 4.10 (settings/theme) remain frozen and are not
touched by these tests.
"""

import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "assets" / "icons"
ASSETS_DIR = ROOT / "assets"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def clean_db():
    """Reset the SQLite database before/after a test that constructs MainWindow."""
    from app.database.connection import get_session, init_db
    from app.database.models import DownloadRecord, SettingRecord

    def _clean():
        init_db()
        session = get_session()
        try:
            session.query(DownloadRecord).delete()
            session.query(SettingRecord).delete()
            session.commit()
        finally:
            session.close()

    _clean()
    yield
    _clean()


# --------------------------------------------------------------------------- #
# Icon assets exist + validity
# --------------------------------------------------------------------------- #
def test_icon_source_assets_exist():
    assert (ICON_DIR / "vanta.svg").is_file()
    assert (ICON_DIR / "vanta.png").is_file()
    assert (ICON_DIR / "vanta.ico").is_file()


def test_ico_contains_standard_windows_resolutions():
    ico = (ICON_DIR / "vanta.ico").read_bytes()
    reserved, typ, count = struct.unpack("<HHH", ico[:6])
    assert reserved == 0
    assert typ == 1  # RT_ICON
    assert count >= 4
    sizes = []
    off = 6
    for _ in range(count):
        entry = struct.unpack("<BBBBHHII", ico[off:off + 16])
        w, h, bpp = entry[0], entry[1], entry[6]
        off += 16
        sizes.append((w or 256, h or 256, bpp))
    present = {s for s, _, _ in sizes}
    # Windows expects at least 16/32/48; 256 is preferred for scaling.
    assert {16, 32, 48, 256} <= present


def test_icon_png_is_valid_image_with_content():
    from PySide6.QtGui import QImage

    img = QImage(str(ICON_DIR / "vanta.png"))
    assert not img.isNull()
    assert img.width() == 256 and img.height() == 256
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    # Center of the icon falls on the opaque arrow shaft -> non-blank.
    center = img.pixel(128, 128)
    assert (center >> 24) & 0xFF > 0


# --------------------------------------------------------------------------- #
# Qt icon wiring
# --------------------------------------------------------------------------- #
def test_icon_resolver_resolves_to_real_file():
    from app.ui.icon import assets_dir, ico_path, icon_path

    p = icon_path()
    assert p.is_file()
    assert p.name == "vanta.png"
    assert ico_path().is_file()
    assert assets_dir().is_dir()


def test_qicon_loads_application_icon(qapp):
    from app.ui.icon import icon

    qicon = icon()
    assert not qicon.isNull()
    assert qicon.availableSizes()  # Qt populated at least one size


def test_main_window_has_window_icon(qapp, clean_db):
    from app.ui.main_window import MainWindow

    window = MainWindow()
    try:
        assert not window.windowIcon().isNull()
    finally:
        window.close()


def test_application_icon_is_loadable_on_qapplication(qapp):
    from PySide6.QtGui import QIcon

    from app.ui.icon import icon_path

    qapp.setWindowIcon(QIcon(str(icon_path())))
    assert not qapp.windowIcon().isNull()


# --------------------------------------------------------------------------- #
# Version resource
# --------------------------------------------------------------------------- #
def test_version_info_file_exists():
    assert (ASSETS_DIR / "VANTA_version_info.txt").is_file()


def test_version_info_contains_required_fields():
    text = (ASSETS_DIR / "VANTA_version_info.txt").read_text(encoding="utf-8")
    assert "VSVersionInfo(" in text
    assert "FixedFileInfo(" in text
    assert "StringFileInfo(" in text
    assert "StringTable(" in text
    assert "VarFileInfo(" in text
    assert "VarStruct(" in text
    for key in (
        "CompanyName",
        "FileDescription",
        "FileVersion",
        "ProductVersion",
        "ProductName",
        "InternalName",
        "OriginalFilename",
        "LegalCopyright",
    ):
        assert key in text


def test_version_info_matches_app_metadata():
    from app.metadata import APP_DESCRIPTION, APP_NAME, APP_VENDOR, APP_VERSION
    from app.version import VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH

    text = (ASSETS_DIR / "VANTA_version_info.txt").read_text(encoding="utf-8")
    assert f"'{APP_NAME}'" in text              # ProductName / InternalName
    assert APP_VERSION in text                  # FileVersion / ProductVersion strings
    assert f"'{APP_VENDOR}'" in text           # CompanyName
    assert APP_DESCRIPTION in text             # FileDescription
    assert f"({VERSION_MAJOR}, {VERSION_MINOR}, {VERSION_PATCH}, 0)" in text  # filevers/prodvers tuple


def test_version_info_is_structurally_valid():
    """Load the version file through PyInstaller's own loader to confirm it
    evaluates to a real VSVersionInfo and serializes to a VS_VERSION_INFO blob.
    Skips when PyInstaller is not installed."""
    pyi = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    info = pyi.load_version_info_from_text_file(str(ASSETS_DIR / "VANTA_version_info.txt"))
    assert isinstance(info, pyi.VSVersionInfo)
    raw = info.toRaw()
    assert isinstance(raw, (bytes, bytearray))
    assert len(raw) > 0


# --------------------------------------------------------------------------- #
# DPI manifest
# --------------------------------------------------------------------------- #
def test_manifest_exists_and_is_dpi_aware():
    manifest = (ASSETS_DIR / "VANTA.manifest").read_text(encoding="utf-8")
    assert "PerMonitorV2" in manifest
    assert "dpiAwareness" in manifest
    assert "dpiAware" in manifest
    # No elevation requested -> asInvoker only.
    assert "asInvoker" in manifest
    assert "requireAdministrator" not in manifest


def test_manifest_is_valid_xml():
    from xml.dom import minidom

    xml = (ASSETS_DIR / "VANTA.manifest").read_text(encoding="utf-8")
    doc = minidom.parseString(xml)
    root = doc.documentElement
    assert root.tagName == "assembly"
    assert root.getAttribute("manifestVersion") == "1.0"


# --------------------------------------------------------------------------- #
# PyInstaller spec references the branding resources (skip if spec absent,
# since VANTA.spec is gitignored and may not exist in a fresh checkout).
# --------------------------------------------------------------------------- #
def test_spec_references_branding_resources():
    spec = Path("VANTA.spec")
    if not spec.is_file():
        pytest.skip("VANTA.spec is gitignored and absent in this checkout")
    text = spec.read_text(encoding="utf-8")
    assert "vanta.ico" in text
    assert "VANTA_version_info.txt" in text
    assert "VANTA.manifest" in text
    assert "console=False" in text
    assert "name='VANTA'" in text


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #
def test_data_log_download_paths_unchanged():
    """M1 must not move the existing writable runtime paths."""
    from app.utils.constants import (
        DATA_DIR,
        DB_PATH,
        DEFAULT_DOWNLOAD_DIR,
        LOG_PATH,
        LOGS_DIR,
    )

    assert DB_PATH == DATA_DIR / "vanta.db"
    assert LOG_PATH == LOGS_DIR / "vanta.log"
    assert LOG_PATH.parent == LOGS_DIR
    assert DEFAULT_DOWNLOAD_DIR == Path.home() / "Downloads" / "VANTA"
    assert DEFAULT_DOWNLOAD_DIR.name == "VANTA"
