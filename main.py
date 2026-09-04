import sys
from pathlib import Path

import asyncio

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from app.database.connection import init_db
from app.ui.main_window import MainWindow
from app.utils.constants import APP_NAME, APP_VERSION
from app.utils.logger import setup_logger

setup_logger()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    init_db()

    style_path = Path(__file__).parent / "assets" / "styles" / "main.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text())

    window = MainWindow()
    window.show()

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    main()
