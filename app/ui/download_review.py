"""V2.0 Phase 2 - Download review dialog.

Shows filename, destination, size, MIME category, and duplicate state
before a download begins.  Reuses existing FileManager and
ResourceIntelligence output - does not modify the download engine.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.services.download_workflow import DownloadWorkflowService
from app.services.duplicate_service import (
    DuplicateCheck,
    DuplicateState,
    duplicate_state_label,
)
from app.services.filename_service import (
    file_category_label,
    format_file_size,
    is_safe_within_directory,
    sanitize_filename,
)
from app.utils.logger import get_logger

log = get_logger("vanta.ui.download_review")


class DownloadReviewDialog(QDialog):
    """Review download details before starting.

    The user may edit the filename and change the destination directory.
    All sanitization goes through ``app.services.filename_service``.
    Duplicate state is recalculated whenever filename or destination changes.
    """

    def __init__(
        self,
        file: DownloadFile,
        file_manager: FileManager,
        download_dir: str | Path | None = None,
        duplicate_check: DuplicateCheck | None = None,
        workflow: DownloadWorkflowService | None = None,
        conflict_policy: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Review Download")
        self.setMinimumSize(520, 420)

        self._file = file
        self._file_manager = file_manager
        self._download_dir = Path(download_dir) if download_dir else file_manager.default_dir
        self._workflow = workflow or DownloadWorkflowService(file_manager)
        self._duplicate_check = duplicate_check or DuplicateCheck(
            state=DuplicateState.READY, detail="Ready to download"
        )

        self._filename = sanitize_filename(file.name)
        self._destination = self._download_dir
        self._conflict_policy = conflict_policy or "auto_rename"
        self._resolved_destination: Path | None = None

        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Confirm Download")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        content = QWidget()
        form = QFormLayout(content)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setSpacing(12)

        self._filename_edit = QLineEdit()
        self._filename_edit.textChanged.connect(self._on_filename_changed)
        form.addRow("Filename:", self._filename_edit)

        self._dest_display = QLineEdit()
        self._dest_display.setReadOnly(True)
        self._dest_display.setFixedHeight(32)

        change_btn = QPushButton("Change Folder")
        change_btn.setFixedSize(120, 32)
        change_btn.clicked.connect(self._on_change_folder)

        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        dest_row.addWidget(self._dest_display, stretch=1)
        dest_row.addWidget(change_btn)
        dest_container = QWidget()
        dest_container.setLayout(dest_row)
        form.addRow("Destination:", dest_container)

        # Conflict policy row (shown when target exists)
        self._conflict_policy_container = QWidget()
        conflict_layout = QHBoxLayout(self._conflict_policy_container)
        conflict_layout.setContentsMargins(0, 0, 0, 0)
        conflict_layout.setSpacing(12)

        self._policy_auto_rename = QRadioButton("Auto Rename")
        self._policy_auto_rename.toggled.connect(self._on_policy_changed)
        self._policy_rename = QRadioButton("Rename")
        self._policy_rename.toggled.connect(self._on_policy_changed)

        conflict_layout.addWidget(self._policy_auto_rename)
        conflict_layout.addWidget(self._policy_rename)
        conflict_layout.addStretch(1)

        form.addRow("If File Exists:", self._conflict_policy_container)
        self._conflict_policy_container.hide()

        # Resolved destination preview (shown when RENAME is selected)
        self._resolved_dest_display = QLineEdit()
        self._resolved_dest_display.setReadOnly(True)
        self._resolved_dest_display.setFixedHeight(32)
        self._resolved_dest_display.hide()
        form.addRow("Will Save As:", self._resolved_dest_display)

        self._size_label = QLabel()
        form.addRow("Size:", self._size_label)

        self._type_label = QLabel()
        form.addRow("Type:", self._type_label)

        self._category_label = QLabel()
        form.addRow("Category:", self._category_label)

        self._url_label = QLabel()
        self._url_label.setWordWrap(True)
        self._url_label.setStyleSheet("font-size: 12px; color: #8A8A9A;")
        form.addRow("Source URL:", self._url_label)

        self._duplicate_label = QLabel()
        self._duplicate_label.setWordWrap(True)
        self._duplicate_label.setStyleSheet("font-size: 12px;")
        form.addRow("Status:", self._duplicate_label)

        scroll.setWidget(content)
        layout.addWidget(scroll, stretch=1)

        button_box = QDialogButtonBox()
        download_btn = QPushButton("Download")
        download_btn.setObjectName("primary_action")
        download_btn.setFixedWidth(120)
        download_btn.clicked.connect(self._on_download)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(120)
        cancel_btn.clicked.connect(self.reject)

        button_box.addButton(download_btn, QDialogButtonBox.AcceptRole)
        button_box.addButton(cancel_btn, QDialogButtonBox.RejectRole)
        layout.addWidget(button_box)

    def _load_values(self):
        self._filename_edit.setText(self._filename)
        self._dest_display.setText(str(self._destination))
        self._size_label.setText(format_file_size(self._file.size))
        self._type_label.setText(self._file.content_type or "Unknown")
        self._category_label.setText(
            self._workflow.format_category_label(
                self._workflow.file_category(self._file, self._file.content_type)
            )
        )
        self._url_label.setText(self._file.url)

        # Set initial conflict policy from settings/default
        if self._conflict_policy == "rename":
            self._policy_rename.setChecked(True)
        else:
            self._policy_auto_rename.setChecked(True)

        self._refresh_duplicate_label()

    def _on_filename_changed(self, text: str):
        self._filename = sanitize_filename(text)
        # Recalculate duplicate state after filename change.
        self._duplicate_check = self._workflow.check_duplicate(
            self._file, self._filename, self._destination
        )
        self._refresh_duplicate_label()

    def _on_policy_changed(self):
        if self._policy_rename.isChecked():
            self._conflict_policy = "rename"
        else:
            self._conflict_policy = "auto_rename"
        self._refresh_duplicate_label()

    def _on_change_folder(self):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Download Location")
        if dialog.exec() == 1:
            selected = dialog.selectedFiles()[0]
            self._destination = Path(selected)
            self._dest_display.setText(str(self._destination))
            # Recalculate duplicate state after destination change.
            self._duplicate_check = self._workflow.check_duplicate(
                self._file, self._filename, self._destination
            )
            self._refresh_duplicate_label()

    def _refresh_duplicate_label(self):
        dup_text = duplicate_state_label(self._duplicate_check.state)
        if self._duplicate_check.detail:
            dup_text = f"{dup_text} - {self._duplicate_check.detail}"
        self._duplicate_label.setText(dup_text)

        if self._duplicate_check.state == DuplicateState.ALREADY_EXISTS:
            self._duplicate_label.setStyleSheet("color: #FFD83D; font-size: 12px;")
            # Show conflict policy options
            self._conflict_policy_container.show()
            # Always resolve destination preview for ALREADY_EXISTS
            self._resolve_destination_preview()
            # Show preview for RENAME (explicit), hide for AUTO_RENAME (silent)
            if self._conflict_policy == "rename":
                self._resolved_dest_display.show()
            else:
                self._resolved_dest_display.hide()
        elif self._duplicate_check.state == DuplicateState.DUPLICATE_RESOURCE:
            self._duplicate_label.setStyleSheet("color: #FF6B6B; font-size: 12px;")
            self._conflict_policy_container.hide()
            self._resolved_dest_display.hide()
        else:
            self._duplicate_label.setStyleSheet("color: #8A8A9A; font-size: 12px;")
            self._conflict_policy_container.hide()
            self._resolved_dest_display.hide()

    def _resolve_destination_preview(self):
        """Resolve and show the destination that will be used when RENAME is selected."""
        safe_name = sanitize_filename(self._filename)
        unique_path = self._file_manager.get_unique_path(safe_name, self._destination)
        self._resolved_destination = unique_path
        self._resolved_dest_display.setText(str(unique_path))

    def _on_download(self):
        if not self._filename:
            log.warning("Download rejected: empty filename")
            self._filename_edit.setFocus()
            return
        if not self._destination or not self._destination.exists():
            log.warning("Download rejected: invalid destination")
            return
        if not self._destination.is_dir():
            log.warning("Download rejected: destination is not a directory")
            return
        if not is_safe_within_directory(self._filename, self._destination):
            log.warning("Download rejected: filename escapes destination")
            return
        if self._duplicate_check.state == DuplicateState.ALREADY_EXISTS:
            # Both AUTO_RENAME and RENAME resolve to a unique safe destination
            self._destination = self._resolved_destination.parent
            self._filename = self._resolved_destination.name
        self.accept()

    @property
    def filename(self) -> str:
        return self._filename

    @property
    def destination(self) -> Path:
        return self._destination

    @property
    def conflict_policy(self) -> str:
        return self._conflict_policy

    @property
    def file(self) -> DownloadFile:
        return self._file


__all__ = ["DownloadReviewDialog"]