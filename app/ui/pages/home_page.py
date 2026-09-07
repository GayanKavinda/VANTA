from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.core.models import AnalysisResult
from app.core.validators import is_valid_url
from app.utils.logger import get_logger

log = get_logger("vanta.ui.home")


class HomePage(QWidget):
    url_analyzed = Signal(str)

    def __init__(self):
        super().__init__()

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("Paste a supported URL")
        self._url_input.setFixedHeight(48)
        self._url_input.returnPressed.connect(self._on_analyze)

        self._analyze_btn = QPushButton("Analyze")
        self._analyze_btn.setFixedSize(120, 48)
        self._analyze_btn.clicked.connect(self._on_analyze)

        url_row = QHBoxLayout()
        url_row.setSpacing(12)
        url_row.addWidget(self._url_input, stretch=1)
        url_row.addWidget(self._analyze_btn)

        url_container = QWidget()
        url_container.setLayout(url_row)

        header = HeaderWidget()
        self._result_area = ResultArea()
        footer = FooterWidget()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(32)

        layout.addWidget(header)
        layout.addWidget(url_container)
        layout.addWidget(self._result_area)
        layout.addWidget(footer)
        layout.addStretch(1)

    def set_current_url_for_result(self, url: str):
        self._result_area.set_current_url(url)

    def _on_analyze(self):
        url = self._url_input.text().strip()

        if not is_valid_url(url):
            log.warning("Invalid URL submitted: %s", url)
            self._result_area.show_error(
                "Invalid URL",
                "Please enter a valid HTTP or HTTPS URL.",
            )
            return

        self._result_area.show_loading()
        self.url_analyzed.emit(url)

    def show_analysis_result(self, result: AnalysisResult):
        if result.status == "unsupported":
            self._result_area.show_unsupported(result.title)
        elif result.status == "error":
            self._result_area.show_error(result.title, "Analysis failed. See logs for details.")
        elif result.files:
            self._result_area.show_files(result)
        else:
            self._result_area.show_unsupported(result.title)

    def show_error(self, title: str, message: str):
        self._result_area.show_error(title, message)

    def clear_result(self):
        self._result_area.clear()


class HeaderWidget(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("VANTA")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")

        subtitle = QLabel("Analyze and manage your downloads from one place.")
        subtitle.setStyleSheet("font-size: 14px; color: #8A8A9A;")

        layout.addWidget(title)
        layout.addWidget(subtitle)


class ResultArea(QWidget):
    def __init__(self):
        super().__init__()

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 14px; color: #8A8A9A;")
        self._status_label.hide()

        self._action_buttons = QHBoxLayout()
        self._action_buttons.setSpacing(12)

        self._open_btn = QPushButton("Open in Browser")
        self._open_btn.setFixedSize(140, 36)

        self._copy_btn = QPushButton("Copy URL")
        self._copy_btn.setFixedSize(110, 36)

        self._download_btn = QPushButton("Start Download")
        self._download_btn.setFixedSize(150, 36)

        self._current_url: str | None = None

        self._open_btn.clicked.connect(self._open_in_browser)
        self._copy_btn.clicked.connect(self._copy_url)

        self._layout.addWidget(self._status_label)
        self._layout.addLayout(self._action_buttons)
        self._layout.addStretch(1)

    def clear(self):
        self._status_label.hide()
        self._current_url = None
        self._action_buttons.removeWidget(self._open_btn)
        self._action_buttons.removeWidget(self._copy_btn)
        self._action_buttons.removeWidget(self._download_btn)

    def show_loading(self):
        self.clear()
        self._status_label.setText("Analyzing...")
        self._status_label.setStyleSheet("font-size: 14px; color: #E8E8EA;")
        self._status_label.show()

    def show_files(self, result: AnalysisResult):
        self.clear()
        files_text = "\n".join(f"• {f.name}" + (f" ({_fmt_size(f.size)})" if f.size else "") for f in result.files)
        self._status_label.setText(f"Found {len(result.files)} file(s):\n{files_text}")
        self._status_label.setStyleSheet("font-size: 14px; color: #E8E8EA; white-space: pre-wrap;")
        self._status_label.show()

        self._download_btn.setText("Start Download")
        self._action_buttons.addWidget(self._download_btn)

    def show_unsupported(self, message: str):
        self.clear()
        self._status_label.setStyleSheet("font-size: 14px; color: #8A8A9A;")
        self._status_label.setText(f"{message}\n\nNo compatible source adapter is available.")
        self._status_label.show()

    def show_error(self, title: str, message: str):
        self.clear()
        self._status_label.setStyleSheet("font-size: 14px; color: #FF6B6B;")
        self._status_label.setText(f"{title}\n\n{message}")
        self._status_label.show()

        self._action_buttons.addWidget(self._open_btn)
        self._action_buttons.addWidget(self._copy_btn)

    def set_current_url(self, url: str):
        self._current_url = url

    def _open_in_browser(self):
        if self._current_url:
            import webbrowser
            webbrowser.open(self._current_url)

    def _copy_url(self):
        if self._current_url:
            from PySide6.QtGui import QGuiApplication
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(self._current_url)
            log.info("URL copied to clipboard")


class FooterWidget(QWidget):
    def __init__(self):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel("Recent Activity")
        label.setStyleSheet("font-size: 14px; font-weight: 600;")

        layout.addWidget(label)
        layout.addStretch(1)

        status = QLabel("No recent downloads")
        status.setStyleSheet("font-size: 13px; color: #8A8A9A;")

        layout.addWidget(status)


def _fmt_size(n: int | float | None) -> str:
    if n is None:
        return ""
    num = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"({num:.1f} {unit})" if unit != "B" else f"({num:.0f} {unit})"
        num /= 1024
    return f"({num:.1f} PB)"
