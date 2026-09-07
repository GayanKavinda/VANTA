import time

from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.connection import get_session
from app.database.models import DownloadRecord, SettingRecord
from app.database.repositories import (
    save_download_task,
    load_download_tasks,
    delete_download_task,
    clear_download_history,
)
from app.services.settings_service import SettingsService, DEFAULTS

import pytest
from sqlalchemy import inspect


def _clean_db():
    session = get_session()
    try:
        session.query(DownloadRecord).delete()
        session.query(SettingRecord).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_db():
    _clean_db()
    yield
    _clean_db()
    SettingsService._instance = None


def test_database_tables_exist():
    session = get_session()
    try:
        tables = inspect(session.bind).get_table_names()
        assert "downloads" in tables
        assert "settings" in tables
    finally:
        session.close()


def test_save_and_load_download_task():
    task = DownloadTask(
        id="test0001",
        name="test_file.zip",
        source_url="https://example.com",
        download_url="https://example.com/test_file.zip",
        destination="/tmp/test_file.zip",
        status=TaskStatus.COMPLETED,
        total_size=1024,
        downloaded_size=1024,
        progress=100.0,
    )

    save_download_task(task)

    loaded = load_download_tasks()
    assert len(loaded) == 1
    assert loaded[0].id == "test0001"
    assert loaded[0].name == "test_file.zip"
    assert loaded[0].status == TaskStatus.COMPLETED


def test_update_existing_task():
    task = DownloadTask(
        id="upd0001",
        name="file.zip",
        source_url="https://example.com",
        download_url="https://example.com/file.zip",
        destination="/tmp/file.zip",
        total_size=4096,
    )

    save_download_task(task)

    task.downloaded_size = 2048
    task.status = TaskStatus.DOWNLOADING
    task.speed = 1.5
    save_download_task(task)

    loaded = load_download_tasks()
    assert len(loaded) == 1
    assert loaded[0].downloaded_size == 2048
    assert loaded[0].status == TaskStatus.DOWNLOADING


def test_delete_download_task():
    task = DownloadTask(
        id="del0001",
        name="file.zip",
        source_url="https://example.com",
        download_url="https://example.com/file.zip",
        destination="/tmp/file.zip",
    )
    save_download_task(task)

    delete_download_task("del0001")

    loaded = load_download_tasks()
    assert len(loaded) == 0


def test_clear_history():
    for i in range(3):
        task = DownloadTask(
            id=f"clr{i}",
            name=f"file{i}.zip",
            source_url="https://example.com",
            download_url=f"https://example.com/file{i}.zip",
            destination="/tmp/file.zip",
        )
        save_download_task(task)

    clear_download_history()

    loaded = load_download_tasks()
    assert len(loaded) == 0


def test_settings_defaults():
    settings = SettingsService()
    assert settings.get("max_concurrent") == DEFAULTS["max_concurrent"]
    assert settings.get("theme") == DEFAULTS["theme"]


def test_settings_set_and_get():
    settings = SettingsService()
    settings.set("max_concurrent", 5)
    assert settings.get_int("max_concurrent") == 5

    settings.set("theme", "light")
    assert settings.get("theme") == "light"

    settings.set("speed_limit_enabled", True)
    assert settings.get_bool("speed_limit_enabled") is True


def test_settings_download_dir():
    settings = SettingsService()
    dir_path = settings.download_dir()
    assert dir_path is not None


def test_error_type_persisted():
    task = DownloadTask(
        id="errtype001",
        name="file.zip",
        source_url="https://example.com",
        download_url="https://example.com/file.zip",
        destination="/tmp/file.zip",
        status=TaskStatus.FAILED,
        error_type=DownloadErrorType.ACCESS_DENIED,
    )

    save_download_task(task)

    loaded = load_download_tasks()
    assert len(loaded) == 1
    assert loaded[0].error_type == DownloadErrorType.ACCESS_DENIED
