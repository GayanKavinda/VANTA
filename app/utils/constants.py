from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

APP_NAME = "VANTA"
APP_VERSION = "1.0.0"

ASSETS_DIR = BASE_DIR / "assets"
STYLES_DIR = ASSETS_DIR / "styles"
ICONS_DIR = ASSETS_DIR / "icons"

DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

DB_PATH = DATA_DIR / "vanta.db"
LOG_PATH = LOGS_DIR / "vanta.log"

DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "VANTA"

DEFAULT_CONCURRENT_DOWNLOADS = 3

SUPPORTED_SCHEMES = ("http", "https")

CHUNK_SIZE = 1024 * 1024
