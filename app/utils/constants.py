"""V2.0 - Application constants and production paths.

This module is the single place where path and version constants live.
It imports the authoritative version from app.version and the
metadata from app.metadata.
"""

import os
from pathlib import Path

from app.metadata import APP_NAME, APP_VERSION
from app.version import VERSION

BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME_CONST = APP_NAME
APP_VERSION_CONST = APP_VERSION

# V2.0 makes these predictable for a packaged Windows application:
#   %LOCALAPPDATA%\VANTA\data     -> database, settings
#   %LOCALAPPDATA%\VANTA\logs     -> log files
#   %USERPROFILE%\Downloads\VANTA  -> default download destination
# When LOCALAPPDATA is unavailable, fall back to BASE_DIR.

_RUNTIME_ROOT = Path(
    os.environ.get("LOCALAPPDATA", BASE_DIR)
) / APP_NAME

DATA_DIR = _RUNTIME_ROOT / "data"
LOGS_DIR = _RUNTIME_ROOT / "logs"
DB_PATH = DATA_DIR / "vanta.db"
LOG_PATH = LOGS_DIR / "vanta.log"

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "VANTA"
DEFAULT_CONCURRENT_DOWNLOADS = 3
SUPPORTED_SCHEMES = ("http", "https")
CHUNK_SIZE = 1024 * 1024

ASSETS_DIR = BASE_DIR / "assets"
STYLES_DIR = ASSETS_DIR / "styles"
ICONS_DIR = ASSETS_DIR / "icons"