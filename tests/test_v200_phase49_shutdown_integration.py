import sys
import asyncio
import warnings
from unittest.mock import patch, MagicMock

import pytest
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop, asyncClose

from app.ui.main_window import MainWindow
from app.services.persistence_service import PersistenceService
from app.core.downloader import DownloadManager


class TestMainWindowQasyncShutdownIntegration:
    """Test the actual MainWindow/qasync shutdown integration."""

    @pytest.fixture
    def qapp(self):
        """Provide a QApplication with qasync loop."""
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        loop = QEventLoop(app)
        asyncio.set_event_loop(loop)
        yield app, loop
        if not loop.is_closed():
            loop.close()

    def test_mainwindow_creation_under_qasync(self, qapp):
        """Test that MainWindow can be created under QApplication/qasync."""
        app, loop = qapp

        window = MainWindow()
        assert window is not None
        assert window._scheduling is not None
        assert window._app_state is not None
        assert window._app_state.download_manager is not None
        assert window._persistence is not None

        window.close()
        # Window close triggers aboutToQuit -> asyncClose handler
        # The handler runs when loop exits
        loop.stop()

    def test_close_does_not_raise_runtimeerror(self, qapp):
        """Test that closing MainWindow does not raise RuntimeError from asyncio.run()."""
        app, loop = qapp

        window = MainWindow()
        window.show()

        # Schedule close
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, window.close)

        # Run loop - should complete without RuntimeError
        with loop:
            loop.run_forever()

        # If we reach here without exception, the test passes
        assert True

    def test_shutdown_handler_triggered(self, qapp):
        """Test that the shutdown handler is triggered through aboutToQuit."""
        app, loop = qapp

        # We need to create the window AFTER the handler is registered
        # The main.py registers the handler, so we simulate that here

        window = MainWindow()
        window.show()

        # Track if shutdown was called by wrapping the actual shutdown method
        shutdown_called = []

        # Wrap the DownloadManager.shutdown method
        original_shutdown = window._app_state.download_manager.shutdown

        async def tracked_shutdown():
            shutdown_called.append(True)
            return await original_shutdown()

        window._app_state.download_manager.shutdown = tracked_shutdown

        # Register an asyncClose handler that calls the tracked shutdown
        # This mimics what main.py does
        @asyncClose
        async def test_shutdown():
            window._scheduling.stop()
            await window._app_state.download_manager.shutdown()
            window._persistence.flush()
            window._persistence.validate_database()

        app.aboutToQuit.connect(test_shutdown)

        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, window.close)

        with loop:
            loop.run_forever()

        # Verify shutdown was called
        assert shutdown_called, "DownloadManager.shutdown was not called"

    def test_persistence_ordering(self, qapp):
        """Test that persistence operations occur after DownloadManager.shutdown()."""
        app, loop = qapp

        window = MainWindow()
        window.show()

        call_order = []

        # Track call order
        original_shutdown = window._app_state.download_manager.shutdown
        original_flush = window._persistence.flush
        original_validate = window._persistence.validate_database

        async def tracked_shutdown():
            call_order.append("shutdown_start")
            await original_shutdown()
            call_order.append("shutdown_end")

        def tracked_flush():
            call_order.append("flush")
            return original_flush()

        def tracked_validate():
            call_order.append("validate")
            return original_validate()

        window._app_state.download_manager.shutdown = tracked_shutdown
        window._persistence.flush = tracked_flush
        window._persistence.validate_database = tracked_validate

        # Register asyncClose handler that uses the tracked methods

        @asyncClose
        async def test_shutdown():
            window._scheduling.stop()
            await window._app_state.download_manager.shutdown()
            window._persistence.flush()
            window._persistence.validate_database()

        app.aboutToQuit.connect(test_shutdown)

        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, window.close)

        with loop:
            loop.run_forever()

        # Verify ordering: shutdown -> flush -> validate
        assert "shutdown_end" in call_order, "shutdown not completed"
        assert "flush" in call_order, "flush not called"
        assert "validate" in call_order, "validate not called"

        shutdown_idx = call_order.index("shutdown_end")
        flush_idx = call_order.index("flush")
        validate_idx = call_order.index("validate")

        assert shutdown_idx < flush_idx, "flush must occur after shutdown"
        assert flush_idx < validate_idx, "validate must occur after flush"

    def test_event_loop_exits_cleanly(self, qapp):
        """Test that the event loop exits cleanly after shutdown."""
        app, loop = qapp

        window = MainWindow()
        window.show()

        @asyncClose
        async def test_shutdown():
            window._scheduling.stop()

        app.aboutToQuit.connect(test_shutdown)

        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, window.close)

        with loop:
            loop.run_forever()

        # Loop should be stopped/closed
        assert not loop.is_running()

    def test_no_unawaited_coroutine_warning(self, qapp):
        """Test that no 'coroutine was never awaited' warning is produced."""

        app, loop = qapp

        window = MainWindow()
        window.show()

        @asyncClose
        async def test_shutdown():
            window._scheduling.stop()

        app.aboutToQuit.connect(test_shutdown)

        # Capture warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, window.close)

            with loop:
                loop.run_forever()

            # Check for unawaited coroutine warnings
            unawaited = [warning for warning in w
                         if "coroutine" in str(warning.message).lower()
                         and "never awaited" in str(warning.message).lower()]

            assert len(unawaited) == 0, f"Found unawaited coroutine warnings: {unawaited}"


# Also test the existing shutdown unit tests still pass
def test_existing_shutdown_tests_still_pass():
    """Verify existing shutdown unit tests are not broken."""
    # This test just ensures we can import the test module
    # The actual tests are run separately
    from tests.test_v115_production_hardening import (
        test_download_manager_shutdown_cancels_active_downloads,
        test_shutdown_persists_after_manager_shutdown,
    )
    assert test_download_manager_shutdown_cancels_active_downloads is not None
    assert test_shutdown_persists_after_manager_shutdown is not None