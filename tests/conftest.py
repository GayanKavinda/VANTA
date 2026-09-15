import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.utils.logger import setup_logger

setup_logger()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _ensure_qapp(qapp):
    """Ensure a QApplication exists before any Qt widget test runs."""
    yield
