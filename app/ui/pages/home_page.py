from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.models import AnalysisResult, DownloadFile
from app.core.validators import is_valid_url
from app.services.analysis_view import (
    AnalysisViewModel,
    ResourceView,
    build_view_model,
)
from app.services.analyzer import ResolutionContext
from app.utils.logger import get_logger

log = get_logger("vanta.ui.home")


_CONFIDENCE_COLORS = {
    "high": "#5AC8FA",
    "medium": "#F0C674",
    "low": "#8A8A9A",
    "rejected": "#FF6B6B",
}


class HomePage(QWidget):
    url_analyzed = Signal(str)
    download_requested = Signal(str, object)

    def __init__(self):
        super().__init__()

        self._current_view: AnalysisViewModel | None = None
        self._current_source_url: str | None = None

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("Paste a page URL (e.g. https://example.com/game-page)")
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
        self._result_area.download_clicked.connect(self._on_resource_download)
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
        self._current_source_url = url
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

        self._current_source_url = url
        self._result_area.set_current_url(url)
        self._result_area.show_loading()
        self.url_analyzed.emit(url)

    def _on_resource_download(self, view: ResourceView):
        if not self._current_source_url:
            log.warning("Download requested without a known source URL")
            return
        self.download_requested.emit(self._current_source_url, view)

    def show_analysis_view(
        self,
        view_model: AnalysisViewModel,
        result: AnalysisResult | None = None,
    ):
        self._current_view = view_model
        if view_model.is_error:
            self._result_area.show_error(view_model.title or "Analysis failed", "")
        elif view_model.is_unsupported or not view_model.has_resources:
            self._result_area.show_unsupported(
                view_model.title or "No downloadable resources found"
            )
        else:
            self._result_area.show_view_model(view_model)

    def show_error(self, title: str, message: str):
        self._result_area.show_error(title, message)

    def clear_result(self):
        self._current_view = None
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
    download_clicked = Signal(object)

    def __init__(self):
        super().__init__()

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        self._title_label.hide()

        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 14px; color: #8A8A9A;")
        self._status_label.setWordWrap(True)
        self._status_label.hide()

        self._action_buttons = QHBoxLayout()
        self._action_buttons.setSpacing(12)

        self._open_btn = QPushButton("Open in Browser")
        self._open_btn.setFixedSize(140, 36)
        self._copy_btn = QPushButton("Copy URL")
        self._copy_btn.setFixedSize(110, 36)

        self._current_url: str | None = None
        self._resources: list[ResourceView] = []

        self._open_btn.clicked.connect(self._open_in_browser)
        self._copy_btn.clicked.connect(self._copy_url)

        self._resource_list = QListWidget()
        self._resource_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; }"
            "QListWidget::item { border: none; }"
        )
        self._resource_list.setSpacing(8)
        self._resource_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._resource_list.hide()

        self._layout.addWidget(self._title_label)
        self._layout.addWidget(self._status_label)
        self._layout.addWidget(self._resource_list)
        self._layout.addLayout(self._action_buttons)
        self._layout.addStretch(1)

    def clear(self):
        self._title_label.hide()
        self._status_label.hide()
        self._resource_list.hide()
        self._resource_list.clear()
        self._resources = []
        self._current_url = None
        for i in reversed(range(self._action_buttons.count())):
            item = self._action_buttons.itemAt(i)
            widget = item.widget() if item else None
            if widget is not None:
                self._action_buttons.removeWidget(widget)

    def show_loading(self):
        self.clear()
        self._status_label.setText("Analyzing...")
        self._status_label.setStyleSheet("font-size: 14px; color: #E8E8EA;")
        self._status_label.show()

    def show_view_model(self, vm: AnalysisViewModel):
        self.clear()
        self._title_label.setText(f"Found {len(vm.resources)} downloadable resource(s)")
        self._title_label.show()

        self._resource_list.clear()
        for rv in vm.resources:
            item = QListWidgetItem(self._resource_list)
            row = ResourceRow(rv)
            item.setSizeHint(row.sizeHint())
            self._resource_list.addItem(item)
            self._resource_list.setItemWidget(item, row)
            row.download_clicked.connect(
                lambda checked=False, r=rv: self._on_row_download(r)
            )
        self._resources = list(vm.resources)
        self._resource_list.show()

    def show_unsupported(self, message: str):
        self.clear()
        self._status_label.setStyleSheet("font-size: 14px; color: #8A8A9A;")
        self._status_label.setText(f"{message}\n\nNo compatible source adapter is available.")
        self._status_label.show()

    def show_error(self, title: str, message: str):
        self.clear()
        self._status_label.setStyleSheet("font-size: 14px; color: #FF6B6B;")
        combined = title if not message else f"{title}\n\n{message}"
        self._status_label.setText(combined)
        self._status_label.show()

        self._action_buttons.addWidget(self._open_btn)
        self._action_buttons.addWidget(self._copy_btn)

    def set_current_url(self, url: str):
        self._current_url = url

    def _on_row_download(self, rv: ResourceView):
        self.download_clicked.emit(rv)

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


class ResourceRow(QFrame):
    download_clicked = Signal()

    def __init__(self, view: ResourceView):
        super().__init__()
        self._view = view

        self.setObjectName("ResourceRow")
        self.setStyleSheet(
            "#ResourceRow { background-color: #1B1B22; border-radius: 10px; padding: 12px; }"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        name_label = QLabel(view.file.name or "(unnamed)")
        name_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #E8E8EA;")
        name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        badge = QLabel(view.confidence_label)
        badge_color = _CONFIDENCE_COLORS.get(view.confidence, "#8A8A9A")
        badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {badge_color};"
            f"background-color: rgba(255,255,255,0.04);"
            f"padding: 2px 8px; border-radius: 8px;"
        )

        size_label = QLabel(view.size_label or "—")
        size_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")

        top_row.addWidget(name_label)
        top_row.addWidget(badge)
        top_row.addWidget(size_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)

        meta_text_parts = []
        if view.type_label:
            meta_text_parts.append(view.type_label)
        if view.reasons:
            meta_text_parts.append(" · ".join(view.reasons[:3]))
        if not meta_text_parts:
            meta_text_parts.append(f"score {view.score}")

        meta_label = QLabel(" · ".join(meta_text_parts))
        meta_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        meta_label.setWordWrap(True)

        download_btn = QPushButton("Download")
        download_btn.setFixedSize(110, 32)
        download_btn.clicked.connect(self.download_clicked.emit)

        meta_row.addWidget(meta_label, stretch=1)
        meta_row.addWidget(download_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        layout.addLayout(top_row)
        layout.addLayout(meta_row)


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