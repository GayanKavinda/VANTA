"""V2.0 Phase 4.6 — Download History & Completed-Download Management.

Tests cover:
- Repository layer: ``load_history_tasks`` returns only terminal records
- HistoryPage: terminal-only filtering, field display, live updates
- HistoryCard: actions, missing-file state, button visibility
- History management: remove (record-only), open file/folder, retry delegation
- Duplicate prevention: restart, refresh, completion events
- Search/filter, empty state, Downloads vs History separation
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PySide6.QtTest import QTest

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.core.queue_controller import QueueController
from app.core.task_manager import DownloadTask, DownloadErrorType, TaskStatus
from app.database.connection import get_session
from app.database.models import DownloadRecord
from app.database.repositories import (
    load_history_tasks,
    save_download_task,
    delete_download_task,
)
from app.services.download_service import DownloadService
from app.services.persistence_service import PersistenceService
from app.ui.pages.history_page import HistoryPage, HistoryCard


# ── Helpers ─────────────────────────────────────────────────────────────────

def _task(tmp_path, task_id, status=TaskStatus.COMPLETED, total_size=1024, **kwargs):
    return DownloadTask(
        id=task_id,
        name=f"{task_id}.zip",
        source_url="https://example.com/page",
        download_url=f"https://cdn.example.com/{task_id}.zip",
        destination=str(tmp_path / f"{task_id}.zip"),
        status=status,
        total_size=total_size,
        **kwargs,
    )


def _cleanup(task_ids):
    session = get_session()
    try:
        session.query(DownloadRecord).filter(
            DownloadRecord.id.in_(task_ids)
        ).delete(synchronize_session=False)
        session.commit()
    finally:
        session.close()


def _clean_history():
    session = get_session()
    try:
        session.query(DownloadRecord).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolate_db():
    _clean_history()
    yield
    _clean_history()


def _env(tmp_path, max_concurrent=1):
    destination = tmp_path / "downloads"
    destination.mkdir()
    manager = DownloadManager(
        max_concurrent=max_concurrent,
        allow_private_networks=True,
        downloads_dir=tmp_path,
    )
    controller = QueueController(manager)
    service = DownloadService(
        analyzer=None,
        download_manager=manager,
        queue_controller=controller,
        file_manager=FileManager(destination),
    )
    return manager, controller, service, destination


async def _wait_for_terminal(task, timeout=2.0):
    for _ in range(int(timeout * 100)):
        if task.is_terminal:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{task.id} did not reach terminal state; got {task.status}")


# ── Repository: load_history_tasks ────────────────────────────────────────────

class TestLoadHistoryTasks:
    """Verify the repository query returns only terminal records."""

    def test_returns_only_terminal_states(self, tmp_path):
        save_download_task(_task(tmp_path, "h_comp", status=TaskStatus.COMPLETED))
        save_download_task(_task(tmp_path, "h_fail", status=TaskStatus.FAILED))
        save_download_task(_task(tmp_path, "h_canc", status=TaskStatus.CANCELLED))
        save_download_task(_task(tmp_path, "h_queued", status=TaskStatus.QUEUED))
        save_download_task(_task(tmp_path, "h_dl", status=TaskStatus.DOWNLOADING))
        save_download_task(_task(tmp_path, "h_paused", status=TaskStatus.PAUSED))
        try:
            ids = {t.id for t in load_history_tasks()}
            assert {"h_comp", "h_fail", "h_canc"} <= ids
            assert "h_queued" not in ids
            assert "h_dl" not in ids
            assert "h_paused" not in ids
        finally:
            _cleanup(["h_comp", "h_fail", "h_canc", "h_queued", "h_dl", "h_paused"])

    def test_preserves_task_identity_and_metadata(self, tmp_path):
        task = _task(
            tmp_path, "h_identity", status=TaskStatus.FAILED,
            total_size=4096, downloaded_size=4096, progress=100.0,
            error="timeout", error_type=DownloadErrorType.TIMEOUT,
        )
        task.created_at = 100.0
        task.updated_at = 200.0
        save_download_task(task)
        try:
            [loaded] = [t for t in load_history_tasks() if t.id == "h_identity"]
            assert loaded.id == "h_identity"
            assert loaded.name == task.name
            assert loaded.source_url == task.source_url
            assert loaded.download_url == task.download_url
            assert loaded.destination == task.destination
            assert loaded.status is TaskStatus.FAILED
            assert loaded.total_size == 4096
            assert loaded.downloaded_size == 4096
            assert loaded.progress == 100.0
            assert loaded.error == "timeout"
            assert loaded.error_type is DownloadErrorType.TIMEOUT
            assert loaded.created_at == 100.0
            assert loaded.updated_at == 200.0
        finally:
            _cleanup(["h_identity"])

    def test_ordered_by_updated_at_desc_then_id(self, tmp_path):
        for task_id, updated_at in [
            ("h_old", 100.0),
            ("h_new", 500.0),
            ("h_mid", 300.0),
        ]:
            task = _task(tmp_path, task_id, status=TaskStatus.COMPLETED)
            task.updated_at = updated_at
            save_download_task(task)
        try:
            ids = [t.id for t in load_history_tasks()]
            assert ids == ["h_new", "h_mid", "h_old"]
        finally:
            _cleanup(["h_old", "h_new", "h_mid"])

    def test_tie_break_by_task_id_is_deterministic(self, tmp_path):
        for task_id in ["h_tie_b", "h_tie_a"]:
            task = _task(tmp_path, task_id, status=TaskStatus.COMPLETED)
            task.updated_at = 500.0  # Same updated_at
            save_download_task(task)
        try:
            ids = [t.id for t in load_history_tasks()]
            assert ids == ["h_tie_a", "h_tie_b"]
        finally:
            _cleanup(["h_tie_b", "h_tie_a"])

    def test_empty_when_no_terminal_tasks(self, tmp_path):
        save_download_task(_task(tmp_path, "h_active", status=TaskStatus.QUEUED))
        try:
            assert load_history_tasks() == []
        finally:
            _cleanup(["h_active"])


# ── HistoryPage: display of terminal records ──────────────────────────────────

class TestHistoryPageDisplay:
    """Verify HistoryPage loads and displays terminal tasks."""

    def test_completed_task_appears_in_history(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_complete", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_complete" in page._cards
        finally:
            _cleanup(["hp_complete"])

    def test_failed_task_appears_in_history(self, qapp, tmp_path):
        save_download_task(_task(
            tmp_path, "hp_fail", status=TaskStatus.FAILED,
            error="timeout", error_type=DownloadErrorType.TIMEOUT,
        ))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_fail" in page._cards
            assert page._cards["hp_fail"]._status_label.text() == "Failed"
        finally:
            _cleanup(["hp_fail"])

    def test_cancelled_task_appears_in_history(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_canc", status=TaskStatus.CANCELLED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_canc" in page._cards
            assert page._cards["hp_canc"]._status_label.text() == "Cancelled"
        finally:
            _cleanup(["hp_canc"])

    def test_queued_task_not_in_history(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_queued", status=TaskStatus.QUEUED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_queued" not in page._cards
            assert len(page._cards) == 0
        finally:
            _cleanup(["hp_queued"])

    def test_downloading_task_not_in_history(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_dl", status=TaskStatus.DOWNLOADING))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_dl" not in page._cards
        finally:
            _cleanup(["hp_dl"])

    def test_task_id_preserved(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_id", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            card = page._cards["hp_id"]
            assert card._task.id == "hp_id"
        finally:
            _cleanup(["hp_id"])

    def test_filename_displayed(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_name", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert page._cards["hp_name"]._name_label.text() == "hp_name.zip"
        finally:
            _cleanup(["hp_name"])

    def test_destination_displayed(self, qapp, tmp_path):
        task = _task(tmp_path, "hp_dest", status=TaskStatus.COMPLETED)
        save_download_task(task)
        try:
            page = HistoryPage()
            QTest.qWait(10)
            detail = page._cards["hp_dest"]._detail_label.text()
            assert str(tmp_path) in detail
        finally:
            _cleanup(["hp_dest"])

    def test_size_displayed(self, qapp, tmp_path):
        save_download_task(_task(
            tmp_path, "hp_size", status=TaskStatus.COMPLETED, total_size=2048,
        ))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            detail = page._cards["hp_size"]._detail_label.text()
            assert "2.0" in detail
            assert "KB" in detail
        finally:
            _cleanup(["hp_size"])

    def test_status_displayed(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_stat", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert page._cards["hp_stat"]._status_label.text() == "Completed"
        finally:
            _cleanup(["hp_stat"])

    def test_error_displayed_for_failed(self, qapp, tmp_path):
        save_download_task(_task(
            tmp_path, "hp_err", status=TaskStatus.FAILED,
            error="connection reset", error_type=DownloadErrorType.CONNECTION_INTERRUPTED,
        ))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            card = page._cards["hp_err"]
            assert card._error_label.text() == "connection reset"
            assert not card._error_label.isHidden()
        finally:
            _cleanup(["hp_err"])

    def test_empty_state_renders(self, qapp):
        page = HistoryPage()
        QTest.qWait(10)
        assert len(page._cards) == 0
        assert "No download history" in page._content_layout.itemAt(0).widget().text()


# ── Live updates ─────────────────────────────────────────────────────────────

class TestHistoryLiveUpdates:
    """Verify HistoryPage updates when a download completes."""

    def test_completion_event_adds_history_entry(self, qapp, tmp_path):
        manager, controller, _, _ = _env(tmp_path, max_concurrent=1)
        persistence = PersistenceService()
        persistence.subscribe_to(manager)
        page = HistoryPage()
        page.set_queue_controller(controller)
        QTest.qWait(10)

        task = _task(tmp_path, "hp_live", status=TaskStatus.QUEUED, queue_order=0)
        manager.register_task(task)
        QTest.qWait(10)
        assert "hp_live" not in page._cards

        manager._set_status(task, TaskStatus.COMPLETED)
        QTest.qWait(20)

        assert "hp_live" in page._cards
        _cleanup(["hp_live"])

    def test_completion_does_not_duplicate(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_nodup", status=TaskStatus.COMPLETED))
        try:
            manager, controller, _, _ = _env(tmp_path, max_concurrent=1)
            page = HistoryPage()
            page.set_queue_controller(controller)
            QTest.qWait(10)

            assert len(page._cards) == 1
            page._on_queue_changed(None)
            QTest.qWait(20)
            assert len(page._cards) == 1
        finally:
            _cleanup(["hp_nodup"])

    def test_refresh_does_not_duplicate(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_refresh", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            for _ in range(3):
                page.refresh()
                QTest.qWait(10)
            assert len(page._cards) == 1
            assert "hp_refresh" in page._cards
        finally:
            _cleanup(["hp_refresh"])

    def test_navigation_reload_stable(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_nav", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert len(page._cards) == 1

            page2 = HistoryPage()
            QTest.qWait(10)
            assert len(page2._cards) == 1
            assert "hp_nav" in page2._cards
        finally:
            _cleanup(["hp_nav"])


# ── History removal ────────────────────────────────────────────────────────────

class TestHistoryRemove:
    """Verify Remove deletes the record but not the physical file."""

    def test_remove_deletes_db_record(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_rm", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_rm" in page._cards

            page._cards["hp_rm"]._remove_btn.click()
            QTest.qWait(10)

            assert "hp_rm" not in page._cards
            remaining = [t.id for t in load_history_tasks()]
            assert "hp_rm" not in remaining
        finally:
            _cleanup(["hp_rm"])

    def test_remove_does_not_delete_file(self, qapp, tmp_path):
        dest = tmp_path / "hp_rmfile.zip"
        dest.write_bytes(b"file content")
        task = _task(tmp_path, "hp_rmfile", status=TaskStatus.COMPLETED)
        task.destination = str(dest)
        save_download_task(task)
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_rmfile" in page._cards

            page._cards["hp_rmfile"]._remove_btn.click()
            QTest.qWait(10)

            assert dest.exists()
            assert dest.read_bytes() == b"file content"
        finally:
            _cleanup(["hp_rmfile"])
            dest.unlink(missing_ok=True)


# ── File-opening actions ────────────────────────────────────────────────────

class TestHistoryFileActions:
    """Verify Open File / Open Folder use QDesktopServices (no subprocess)."""

    def test_open_folder_uses_qdesktopservices(self, qapp, tmp_path, monkeypatch):
        dest = tmp_path / "hp_folder.zip"
        dest.write_bytes(b"data")
        task = _task(tmp_path, "hp_folder", status=TaskStatus.COMPLETED)
        task.destination = str(dest)
        save_download_task(task)
        try:
            page = HistoryPage()
            QTest.qWait(10)

            opened = []
            monkeypatch.setattr(
                "app.ui.pages.history_page.QDesktopServices.openUrl",
                lambda url: opened.append(url.toLocalFile()),
            )
            page._on_open_folder("hp_folder")

            assert len(opened) == 1
        finally:
            _cleanup(["hp_folder"])
            dest.unlink(missing_ok=True)

    def test_open_file_uses_qdesktopservices(self, qapp, tmp_path, monkeypatch):
        dest = tmp_path / "hp_file.zip"
        dest.write_bytes(b"data")
        task = _task(tmp_path, "hp_file", status=TaskStatus.COMPLETED)
        task.destination = str(dest)
        save_download_task(task)
        try:
            page = HistoryPage()
            QTest.qWait(10)

            opened = []
            monkeypatch.setattr(
                "app.ui.pages.history_page.QDesktopServices.openUrl",
                lambda url: opened.append(url.toLocalFile()),
            )
            page._on_open_file("hp_file")

            assert len(opened) == 1
            assert Path(opened[0]) == dest
        finally:
            _cleanup(["hp_file"])
            dest.unlink(missing_ok=True)

    def test_missing_file_shows_not_found(self, qapp, tmp_path):
        task = _task(tmp_path, "hp_missing", status=TaskStatus.COMPLETED)
        task.destination = str(tmp_path / "nonexistent.zip")
        save_download_task(task)
        try:
            page = HistoryPage()
            QTest.qWait(10)
            card = page._cards["hp_missing"]
            assert not card._missing_label.isHidden()
            assert "File not found" in card._missing_label.text()
            assert card._open_file_btn.isHidden()
        finally:
            _cleanup(["hp_missing"])

    def test_cancelled_no_open_file_button(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_canc_btn", status=TaskStatus.CANCELLED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            card = page._cards["hp_canc_btn"]
            assert card._open_file_btn.isHidden()
            assert card._retry_btn.isHidden()
        finally:
            _cleanup(["hp_canc_btn"])

    def test_failed_shows_retry_button(self, qapp, tmp_path):
        save_download_task(_task(
            tmp_path, "hp_retry", status=TaskStatus.FAILED,
            error="timeout", error_type=DownloadErrorType.TIMEOUT,
        ))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            card = page._cards["hp_retry"]
            assert not card._retry_btn.isHidden()
        finally:
            _cleanup(["hp_retry"])


# ── Retry delegation ────────────────────────────────────────────────────────

class TestHistoryRetry:
    """Verify retry delegates to the public queue controller API."""

    def test_retry_delegates_to_controller(self, qapp, tmp_path, monkeypatch):
        manager, controller, _, _ = _env(tmp_path, max_concurrent=0)
        task = _task(
            tmp_path, "hp_retry_dlg", status=TaskStatus.FAILED,
            error="timeout", error_type=DownloadErrorType.TIMEOUT,
        )
        manager.register_task(task)
        save_download_task(task)
        try:
            page = HistoryPage()
            page.set_queue_controller(controller)
            QTest.qWait(10)

            called = []
            monkeypatch.setattr(controller, "retry_download",
                                lambda tid: called.append(tid))
            page._cards["hp_retry_dlg"]._retry_btn.click()
            QTest.qWait(5)

            assert called == ["hp_retry_dlg"]
        finally:
            _cleanup(["hp_retry_dlg"])

    def test_completed_task_has_no_retry_button(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_no_retry", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            card = page._cards["hp_no_retry"]
            assert card._retry_btn.isHidden()
        finally:
            _cleanup(["hp_no_retry"])


# ── Search / filtering ───────────────────────────────────────────────────────

class TestHistorySearch:
    """Verify search filtering is case-insensitive and non-mutating."""

    def test_search_filters_by_filename(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_s_alpha", status=TaskStatus.COMPLETED))
        save_download_task(_task(tmp_path, "hp_s_beta", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert len(page._cards) == 2

            page._search_input.setText("alpha")
            QTest.qWait(5)
            assert len(page._cards) == 1
            assert "hp_s_alpha" in page._cards
        finally:
            _cleanup(["hp_s_alpha", "hp_s_beta"])

    def test_search_filters_by_destination(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_s_dest", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            page._search_input.setText(tmp_path.name)
            QTest.qWait(5)
            assert len(page._cards) == 1
        finally:
            _cleanup(["hp_s_dest"])

    def test_search_case_insensitive(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_s_case", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            page._search_input.setText("HP_S_CASE")
            QTest.qWait(5)
            assert len(page._cards) == 1
        finally:
            _cleanup(["hp_s_case"])

    def test_clear_search_shows_all(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_s_clear_a", status=TaskStatus.COMPLETED))
        save_download_task(_task(tmp_path, "hp_s_clear_b", status=TaskStatus.COMPLETED))
        try:
            page = HistoryPage()
            QTest.qWait(10)
            page._search_input.setText("alpha")
            QTest.qWait(5)
            page._search_input.setText("")
            QTest.qWait(5)
            assert len(page._cards) == 2
        finally:
            _cleanup(["hp_s_clear_a", "hp_s_clear_b"])


# ── Downloads vs History separation ──────────────────────────────────────────

class TestDownloadsHistorySeparation:
    """Verify active/queued tasks do not appear in History."""

    def test_active_task_not_in_history_page(self, qapp, tmp_path):
        manager, controller, _, _ = _env(tmp_path, max_concurrent=0)
        page = HistoryPage()
        page.set_queue_controller(controller)
        QTest.qWait(10)

        task = _task(tmp_path, "hp_sep", status=TaskStatus.QUEUED, queue_order=0)
        manager.register_task(task)
        QTest.qWait(20)

        assert "hp_sep" not in page._cards
        _cleanup(["hp_sep"])

    def test_downloads_page_and_history_independent(self, qapp, tmp_path):
        from app.ui.pages.downloads_page import DownloadsPage

        manager, controller, _, _ = _env(tmp_path, max_concurrent=0)
        downloads = DownloadsPage()
        downloads.set_queue_controller(controller)
        history = HistoryPage()
        history.set_queue_controller(controller)
        QTest.qWait(10)

        task = _task(tmp_path, "hp_both", status=TaskStatus.QUEUED, queue_order=0)
        manager.register_task(task)
        QTest.qWait(20)

        assert task.id in downloads._cards
        assert task.id not in history._cards
        _cleanup(["hp_both"])


# ── Restart / duplication prevention ─────────────────────────────────────────

class TestHistoryRestart:
    """Verify restart does not duplicate history entries."""

    def test_completed_task_survives_restart_without_duplicate(self, qapp, tmp_path):
        save_download_task(_task(tmp_path, "hp_restart", status=TaskStatus.COMPLETED))
        try:
            page1 = HistoryPage()
            QTest.qWait(10)
            assert len(page1._cards) == 1

            page2 = HistoryPage()
            QTest.qWait(10)
            assert len(page2._cards) == 1
            assert "hp_restart" in page2._cards
        finally:
            _cleanup(["hp_restart"])

    def test_failed_task_survives_restart(self, qapp, tmp_path):
        task = _task(
            tmp_path, "hp_restart_fail", status=TaskStatus.FAILED,
            error="connection reset", error_type=DownloadErrorType.CONNECTION_INTERRUPTED,
        )
        save_download_task(task)
        try:
            page = HistoryPage()
            QTest.qWait(10)
            assert "hp_restart_fail" in page._cards
            assert page._cards["hp_restart_fail"]._status_label.text() == "Failed"
        finally:
            _cleanup(["hp_restart_fail"])

    def test_live_completion_then_restart_no_duplicate(self, qapp, tmp_path):
        manager, controller, _, _ = _env(tmp_path, max_concurrent=1)
        persistence = PersistenceService()
        persistence.subscribe_to(manager)
        page = HistoryPage()
        page.set_queue_controller(controller)
        QTest.qWait(10)

        task = _task(tmp_path, "hp_restart_live", status=TaskStatus.QUEUED, queue_order=0)
        manager.register_task(task)
        QTest.qWait(5)

        manager._set_status(task, TaskStatus.COMPLETED)
        QTest.qWait(20)

        assert len(page._cards) == 1
        assert "hp_restart_live" in page._cards

        page2 = HistoryPage()
        QTest.qWait(10)
        assert len(page2._cards) == 1
        assert "hp_restart_live" in page2._cards

        _cleanup(["hp_restart_live"])


# ── HistoryCard unit tests ──────────────────────────────────────────────────

class TestHistoryCardRendering:
    """Verify HistoryCard renders correct fields and button visibility."""

    def test_completed_card_shows_open_file_when_file_exists(self, qapp, tmp_path):
        dest = tmp_path / "hp_card.zip"
        dest.write_bytes(b"data")
        task = _task(tmp_path, "hp_card", status=TaskStatus.COMPLETED)
        task.destination = str(dest)
        card = HistoryCard(task)
        QTest.qWait(5)
        assert not card._open_file_btn.isHidden()
        assert not card._open_folder_btn.isHidden()
        assert card._retry_btn.isHidden()
        assert not card._remove_btn.isHidden()

    def test_completed_card_shows_missing_when_file_absent(self, qapp, tmp_path):
        task = _task(tmp_path, "hp_card_missing", status=TaskStatus.COMPLETED)
        task.destination = str(tmp_path / "ghost.zip")
        card = HistoryCard(task)
        QTest.qWait(5)
        assert card._open_file_btn.isHidden()
        assert not card._missing_label.isHidden()
        assert not card._open_folder_btn.isHidden()

    def test_failed_card_shows_error_and_retry(self, qapp, tmp_path):
        task = _task(
            tmp_path, "hp_card_fail", status=TaskStatus.FAILED,
            error="network error", error_type=DownloadErrorType.NETWORK,
        )
        card = HistoryCard(task)
        QTest.qWait(5)
        assert card._status_label.text() == "Failed"
        assert not card._retry_btn.isHidden()
        assert card._open_file_btn.isHidden()

    def test_cancelled_card_no_open_file_no_retry(self, qapp, tmp_path):
        task = _task(tmp_path, "hp_card_cancel", status=TaskStatus.CANCELLED)
        card = HistoryCard(task)
        QTest.qWait(5)
        assert card._status_label.text() == "Cancelled"
        assert card._retry_btn.isHidden()
        assert card._open_file_btn.isHidden()

    def test_card_signals_emit_task_id(self, qapp, tmp_path):
        task = _task(tmp_path, "hp_signals", status=TaskStatus.COMPLETED)
        card = HistoryCard(task)
        QTest.qWait(5)

        emitted = []
        card.open_file_requested.connect(emitted.append)
        card.open_folder_requested.connect(emitted.append)
        card.retry_requested.connect(emitted.append)
        card.remove_requested.connect(emitted.append)

        card._open_file_btn.click()
        card._open_folder_btn.click()
        card._retry_btn.click()
        card._remove_btn.click()
        QTest.qWait(5)

        assert emitted == ["hp_signals"] * 4
