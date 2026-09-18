"""V2.0 entry point.

Initialises runtime directories, sets application metadata on the
QApplication, loads the main window, and runs the event loop.
"""

import sys
from pathlib import Path

import asyncio

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop, asyncClose

from app.database.connection import init_db
from app.metadata import APP_DESCRIPTION, APP_NAME, APP_VERSION, APP_VENDOR
from app.ui.main_window import MainWindow
from app.utils.constants import DATA_DIR, LOGS_DIR
from app.utils.logger import setup_logger


def _ensure_runtime_dirs():
    """Create required runtime directories for a clean first start."""
    for directory in (DATA_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def main():
    _ensure_runtime_dirs()
    setup_logger()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_VENDOR)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    init_db()

    style_path = Path(__file__).parent / "assets" / "styles" / "main.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text())

    window = MainWindow()
    window.show()

    # Register async shutdown handler - runs on qasync loop during aboutToQuit
    @asyncClose
    async def _shutdown():
        try:
            window._scheduling.stop()
        except Exception as e:
            from app.utils.logger import get_logger
            log = get_logger("vanta.shutdown")
            log.error("SchedulingService.stop failed: %s", e, exc_info=True)

        try:
            await window._app_state.download_manager.shutdown()
        except Exception as e:
            from app.utils.logger import get_logger
            log = get_logger("vanta.shutdown")
            log.error("DownloadManager.shutdown failed: %s", e, exc_info=True)

        try:
            window._persistence.flush()
        except Exception as e:
            from app.utils.logger import get_logger
            log = get_logger("vanta.shutdown")
            log.error("PersistenceService.flush failed: %s", e, exc_info=True)

        try:
            window._persistence.validate_database()
        except Exception as e:
            from app.utils.logger import get_logger
            log = get_logger("vanta.shutdown")
            log.error("PersistenceService.validate_database failed: %s", e, exc_info=True)

    app.aboutToQuit.connect(_shutdown)

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()