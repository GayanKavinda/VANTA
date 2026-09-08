from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.services.settings_service import SettingsService
from app.utils.logger import get_logger

log = get_logger("vanta.ui.settings")


class SettingsPage(QWidget):
    settings_changed = Signal(str, str)

    def __init__(self, settings_service=None):
        super().__init__()
        self._settings = settings_service or SettingsService()
        self._download_manager: DownloadManager | None = None
        self._file_manager: FileManager | None = None

        self._build_ui()
        self._load_values()

    def set_services(self, download_manager: DownloadManager, file_manager: FileManager, settings_service=None):
        self._download_manager = download_manager
        self._file_manager = file_manager
        if settings_service is not None:
            self._settings = settings_service

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(48, 48, 48, 48)
        main_layout.setSpacing(16)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        main_layout.addWidget(title)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 24)
        scroll_layout.setSpacing(24)

        downloads_group = self._build_downloads_section()
        appearance_group = self._build_appearance_section()
        application_group = self._build_application_section()

        scroll_layout.addWidget(downloads_group)
        scroll_layout.addWidget(appearance_group)
        scroll_layout.addWidget(application_group)
        scroll_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(scroll_content)

        main_layout.addWidget(scroll, stretch=1)

    def _build_downloads_section(self) -> QFrame:
        group = self._section_frame("Downloads")
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 32, 20, 20)
        layout.setSpacing(16)

        self._dir_display = QLineEdit()
        self._dir_display.setReadOnly(True)
        self._dir_display.setFixedHeight(36)

        change_btn = QPushButton("Change Location")
        change_btn.setFixedSize(130, 36)
        change_btn.clicked.connect(self._on_change_dir)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(12)
        dir_row.addWidget(self._dir_display, stretch=1)
        dir_row.addWidget(change_btn)
        layout.addLayout(dir_row)

        self._concurrent_spin = QSpinBox()
        self._concurrent_spin.setRange(1, 20)
        self._concurrent_spin.setFixedHeight(28)
        self._concurrent_spin.valueChanged.connect(self._on_concurrent_changed)
        layout.addLayout(self._labeled_row("Maximum Concurrent Downloads", self._concurrent_spin))

        self._speed_limit_check = QCheckBox("Enable speed limit")
        self._speed_limit_check.setChecked(self._settings.get_bool("speed_limit_enabled"))
        self._speed_limit_check.toggled.connect(self._on_speed_limit_toggled)

        self._speed_limit_spin = QSpinBox()
        self._speed_limit_spin.setRange(1, 1000)
        self._speed_limit_spin.setSuffix(" MB/s")
        self._speed_limit_spin.setFixedHeight(28)
        self._speed_limit_spin.setEnabled(self._speed_limit_check.isChecked())
        self._speed_limit_check.toggled.connect(
            lambda checked: self._speed_limit_spin.setEnabled(checked)
        )
        self._speed_limit_spin.valueChanged.connect(self._on_speed_limit_changed)

        layout.addWidget(self._speed_limit_check)
        layout.addWidget(self._speed_limit_spin)

        group.layout().addLayout(layout)
        return group

    def _build_appearance_section(self) -> QFrame:
        group = self._section_frame("Appearance")
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 32, 20, 20)
        layout.setSpacing(16)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(12)
        theme_label = QLabel("Theme")
        theme_label.setStyleSheet("font-size: 13px;")

        self._theme_combo = QComboBox()
        self._theme_combo.setFixedHeight(28)
        self._theme_combo.setMinimumWidth(150)
        self._theme_combo.addItems(["Dark", "Light"])
        current_theme = self._settings.get("theme", "dark")
        self._theme_combo.setCurrentText(current_theme.capitalize())
        self._theme_combo.currentTextChanged.connect(self._on_theme_changed)

        theme_row.addWidget(theme_label)
        theme_row.addWidget(self._theme_combo)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        group.layout().addLayout(layout)
        return group

    def _build_application_section(self) -> QFrame:
        group = self._section_frame("Application")
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 32, 20, 20)
        layout.setSpacing(12)

        self._startup_check = QCheckBox("Launch on startup")
        self._startup_check.setChecked(self._settings.get_bool("launch_on_startup"))
        self._startup_check.toggled.connect(self._on_startup_toggled)
        layout.addWidget(self._startup_check)

        self._updates_check = QCheckBox("Check for updates")
        self._updates_check.setChecked(self._settings.get_bool("check_for_updates"))
        self._updates_check.toggled.connect(self._on_updates_toggled)
        layout.addWidget(self._updates_check)

        group.layout().addLayout(layout)
        return group

    def _section_frame(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("settings_section")
        frame.setStyleSheet("""
            QFrame#settings_section {
                background: #111217;
                border-radius: 12px;
            }
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel(title)
        header.setStyleSheet("font-size: 13px; font-weight: 600; padding-left: 20px; padding-top: 16px; color: #8A8A9A;")
        layout.addWidget(header)

        return frame

    def _labeled_row(self, label: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 13px;")
        row.addWidget(lbl)
        row.addWidget(widget)
        return row

    def _load_values(self):
        self._dir_display.setText(self._settings.get("download_dir"))
        self._concurrent_spin.setValue(self._settings.get_int("max_concurrent", 3))
        self._speed_limit_spin.setValue(
            int(float(self._settings.get("speed_limit_value", "10")))
        )

    def _on_change_dir(self):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Download Location")

        if dialog.exec() == 1:
            selected = dialog.selectedFiles()[0]
            self._settings.set("download_dir", selected)
            self._dir_display.setText(selected)
            if self._file_manager:
                self._file_manager.set_default_dir(Path(selected))
            self.settings_changed.emit("download_dir", selected)
            log.info("Download directory changed to: %s", selected)

    def _on_concurrent_changed(self, value: int):
        self._settings.set("max_concurrent", value)
        self.settings_changed.emit("max_concurrent", str(value))
        log.info("Max concurrent downloads set to: %d", value)

    def _on_speed_limit_toggled(self, checked: bool):
        self._settings.set("speed_limit_enabled", checked)
        self.settings_changed.emit("speed_limit_enabled", str(checked))

    def _on_speed_limit_changed(self, value: int):
        self._settings.set("speed_limit_value", value)
        self.settings_changed.emit("speed_limit_value", str(value))

    def _on_theme_changed(self, text: str):
        theme = text.lower()
        self._settings.set("theme", theme)
        self.settings_changed.emit("theme", theme)
        log.info("Theme set to: %s", theme)

    def _on_startup_toggled(self, checked: bool):
        self._settings.set("launch_on_startup", checked)
        self.settings_changed.emit("launch_on_startup", str(checked))

    def _on_updates_toggled(self, checked: bool):
        self._settings.set("check_for_updates", checked)
        self.settings_changed.emit("check_for_updates", str(checked))
