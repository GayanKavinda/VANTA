import os
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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.downloader import DownloadManager
from app.core.file_manager import FileManager
from app.metadata import about_text, app_version_string
from app.services.settings_service import DEFAULTS, SettingsService
from app.utils.logger import get_logger

log = get_logger("vanta.ui.settings")


class SettingsPage(QWidget):
    settings_changed = Signal(str, str)

    def __init__(self, settings_service=None):
        super().__init__()
        self._settings = settings_service or SettingsService()
        self._download_manager: DownloadManager | None = None
        self._file_manager: FileManager | None = None
        self._dir_error: str | None = None

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
        group = self._section_frame(
            "Downloads",
            "Choose where files are saved and control download behavior.",
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(16)

        self._dir_display = QLineEdit()
        self._dir_display.setReadOnly(True)
        self._dir_display.setFixedHeight(36)

        self._dir_error_label = QLabel()
        self._dir_error_label.setStyleSheet("color: #FF6B6B; font-size: 12px;")
        self._dir_error_label.setWordWrap(True)
        self._dir_error_label.hide()

        change_btn = QPushButton("Change Location")
        change_btn.setFixedSize(130, 36)
        change_btn.clicked.connect(self._on_change_dir)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(12)
        dir_row.addWidget(self._dir_display, stretch=1)
        dir_row.addWidget(change_btn)
        layout.addLayout(dir_row)
        layout.addWidget(self._dir_error_label)

        self._concurrent_spin = QSpinBox()
        self._concurrent_spin.setRange(1, 20)
        self._concurrent_spin.setFixedHeight(28)
        self._concurrent_spin.valueChanged.connect(self._on_concurrent_changed)
        layout.addLayout(self._labeled_row("Maximum Concurrent Downloads", self._concurrent_spin))

        self._conflict_combo = QComboBox()
        self._conflict_combo.setFixedHeight(28)
        self._conflict_combo.setMinimumWidth(150)
        self._conflict_combo.addItems(["Auto Rename", "Rename"])
        current_policy = self._settings.conflict_policy()
        self._conflict_combo.setCurrentText("Auto Rename" if current_policy == "auto_rename" else "Rename")
        self._conflict_combo.currentTextChanged.connect(self._on_conflict_policy_changed)
        layout.addLayout(self._labeled_row("If File Exists", self._conflict_combo))

        self._speed_limit_check = QCheckBox("Limit download speed")
        self._speed_limit_check.setChecked(self._settings.get_bool("speed_limit_enabled"))
        self._speed_limit_check.toggled.connect(self._on_speed_limit_toggled)

        self._speed_limit_note = QLabel(
            "Each active download is capped at this speed individually. "
            "Several downloads running at the same time each respect this limit - "
            "the total is not shared."
        )
        self._speed_limit_note.setStyleSheet(
            "font-size: 12px; color: #8A8A9A;"
        )
        self._speed_limit_note.setWordWrap(True)
        self._speed_limit_note.setEnabled(self._speed_limit_check.isChecked())

        self._speed_limit_spin = QSpinBox()
        self._speed_limit_spin.setRange(1, 1000)
        self._speed_limit_spin.setSuffix(" MB/s")
        self._speed_limit_spin.setFixedHeight(28)
        self._speed_limit_spin.setEnabled(self._speed_limit_check.isChecked())
        self._speed_limit_check.toggled.connect(
            lambda checked: self._speed_limit_spin.setEnabled(checked)
        )
        self._speed_limit_check.toggled.connect(
            lambda checked: self._speed_limit_note.setEnabled(checked)
        )
        self._speed_limit_spin.valueChanged.connect(self._on_speed_limit_changed)

        spinedit_row = self._labeled_row(
            "Maximum speed per download:", self._speed_limit_spin
        )

        layout.addWidget(self._speed_limit_check)
        layout.addWidget(self._speed_limit_note)
        layout.addLayout(spinedit_row)

        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        self._reset_btn = QPushButton("Reset to Defaults")
        self._reset_btn.setFixedSize(160, 32)
        self._reset_btn.clicked.connect(self._on_reset_to_defaults)
        reset_row.addWidget(self._reset_btn)
        layout.addLayout(reset_row)

        group.layout().addLayout(layout)
        return group

    def _build_appearance_section(self) -> QFrame:
        group = self._section_frame(
            "Appearance",
            "Customize how VANTA looks on your screen.",
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 24, 20, 20)
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
        group = self._section_frame(
            "Application",
            "General application preferences.",
        )
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(12)

        self._startup_check = QCheckBox("Launch on startup")
        self._startup_check.setChecked(self._settings.get_bool("launch_on_startup"))
        self._startup_check.toggled.connect(self._on_startup_toggled)
        self._startup_check.setEnabled(False)
        startup_note = QLabel("Deferred - startup registration is not available in this version.")
        startup_note.setStyleSheet("font-size: 11px; color: #5A5A66;")
        startup_note.setWordWrap(True)
        layout.addWidget(self._startup_check)
        layout.addWidget(startup_note)

        self._updates_check = QCheckBox("Check for updates")
        self._updates_check.setChecked(self._settings.get_bool("check_for_updates"))
        self._updates_check.toggled.connect(self._on_updates_toggled)
        self._updates_check.setEnabled(False)
        updates_note = QLabel("Deferred - the update checker is not available in this version.")
        updates_note.setStyleSheet("font-size: 11px; color: #5A5A66;")
        updates_note.setWordWrap(True)
        layout.addWidget(self._updates_check)
        layout.addWidget(updates_note)

        # V2.0 - About / version visibility
        about_frame = QFrame()
        about_frame.setStyleSheet("background: transparent;")
        about_layout = QVBoxLayout(about_frame)
        about_layout.setContentsMargins(0, 0, 0, 0)
        about_layout.setSpacing(4)

        version_label = QLabel(app_version_string())
        version_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #E8E8F0;")
        about_layout.addWidget(version_label)

        about_body = QLabel(about_text())
        about_body.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        about_body.setWordWrap(True)
        about_layout.addWidget(about_body)

        layout.addWidget(about_frame)

        group.layout().addLayout(layout)
        return group

    def _section_frame(self, title: str, description: str | None = None) -> QFrame:
        frame = QFrame()
        frame.setObjectName("settings_section")
        frame.setStyleSheet("")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel(title)
        header.setStyleSheet("font-size: 13px; font-weight: 600; padding-left: 20px; padding-top: 16px; color: #8A8A9A;")
        layout.addWidget(header)

        if description:
            desc = QLabel(description)
            desc.setStyleSheet("font-size: 12px; padding-left: 20px; padding-right: 20px; padding-bottom: 8px; color: #5A5A66;")
            desc.setWordWrap(True)
            layout.addWidget(desc)

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
        self._dir_error_label.hide()
        self._dir_error_label.setText("")
        self._dir_error = None
        self._concurrent_spin.setValue(self._settings.get_int("max_concurrent", 3))
        self._speed_limit_spin.setValue(
            int(float(self._settings.get("speed_limit_value", "10")))
        )
        self._theme_combo.setCurrentText(self._settings.get("theme", "dark").capitalize())
        conflict_policy = self._settings.conflict_policy()
        self._conflict_combo.setCurrentText("Auto Rename" if conflict_policy == "auto_rename" else "Rename")
        self._speed_limit_check.setChecked(self._settings.get_bool("speed_limit_enabled"))
        self._speed_limit_spin.setEnabled(self._speed_limit_check.isChecked())
        self._speed_limit_note.setEnabled(self._speed_limit_check.isChecked())
        self._startup_check.setChecked(self._settings.get_bool("launch_on_startup"))
        self._updates_check.setChecked(self._settings.get_bool("check_for_updates"))

    @staticmethod
    def _validate_download_dir(path_str: str) -> str | None:
        """Return an error message if ``path_str`` is not a usable download dir.

        Returns ``None`` when the path is acceptable. Performs no destructive
        operations and creates no files.
        """
        if not path_str or not path_str.strip():
            return "No directory selected."
        path = Path(path_str)
        try:
            resolved = path.resolve()
        except OSError:
            return "That path could not be resolved."

        if resolved.exists():
            if not resolved.is_dir():
                return "That path is a file, not a directory."
            if not os.access(resolved, os.W_OK):
                return "That directory is not writable."
            return None

        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"Could not create that directory: {exc}"
        if not os.access(resolved, os.W_OK):
            return "That directory is not writable."
        return None

    def _on_change_dir(self):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Download Location")

        if dialog.exec() != 1:
            return

        selected = dialog.selectedFiles()[0]
        error = self._validate_download_dir(selected)
        if error is not None:
            self._dir_error = error
            self._dir_error_label.setText(error)
            self._dir_error_label.show()
            log.warning("Download directory rejected: %s (%s)", selected, error)
            return

        self._dir_error = None
        self._dir_error_label.hide()
        self._dir_error_label.setText("")

        self._settings.set("download_dir", selected)
        self._dir_display.setText(selected)
        if self._file_manager:
            self._file_manager.set_default_dir(Path(selected))
        self.settings_changed.emit("download_dir", selected)
        log.info("Download directory changed to: %s", selected)
        self._show_save_feedback("Download directory updated")

    def _on_reset_to_defaults(self):
        reply = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Restore all settings to their default values?\n\n"
            "This only affects preferences. Downloads, history, and "
            "scheduled tasks are not changed.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        restored = self._settings.reset_to_defaults()
        self._load_values()
        self.settings_changed.emit("download_dir", restored["download_dir"])
        self.settings_changed.emit("max_concurrent", restored["max_concurrent"])
        self.settings_changed.emit("speed_limit_enabled", restored["speed_limit_enabled"])
        self.settings_changed.emit("speed_limit_value", restored["speed_limit_value"])
        self.settings_changed.emit("theme", restored["theme"])
        self.settings_changed.emit("conflict_policy", restored["conflict_policy"])
        self.settings_changed.emit("launch_on_startup", restored["launch_on_startup"])
        self.settings_changed.emit("check_for_updates", restored["check_for_updates"])
        log.info("Settings reset to defaults: %s", ", ".join(sorted(restored)))

    def _on_concurrent_changed(self, value: int):
        self._settings.set("max_concurrent", value)
        self.settings_changed.emit("max_concurrent", str(value))
        log.info("Max concurrent downloads set to: %d", value)

    def _on_speed_limit_toggled(self, checked: bool):
        self._settings.set("speed_limit_enabled", checked)
        self.settings_changed.emit("speed_limit_enabled", str(checked))
        self._speed_limit_spin.setEnabled(checked)
        self._speed_limit_note.setEnabled(checked)

    def _on_speed_limit_changed(self, value: int):
        self._settings.set("speed_limit_value", value)
        self.settings_changed.emit("speed_limit_value", str(value))

    def _on_conflict_policy_changed(self, text: str):
        policy = "auto_rename" if text == "Auto Rename" else "rename"
        self._settings.set("conflict_policy", policy)
        self.settings_changed.emit("conflict_policy", policy)
        log.info("Conflict policy set to: %s", policy)

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

    def _show_save_feedback(self, message: str):
        """Show a brief save confirmation message."""
        self._dir_error_label.setStyleSheet("color: #5AC8FA; font-size: 12px;")
        self._dir_error_label.setText(message)
        self._dir_error_label.show()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self._clear_save_feedback())

    def _clear_save_feedback(self):
        if self._dir_error is None:
            self._dir_error_label.hide()
            self._dir_error_label.setText("")
            self._dir_error_label.setStyleSheet("color: #FF6B6B; font-size: 12px;")
