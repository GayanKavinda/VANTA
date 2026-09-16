"""V2.0 Phase 3.8 — Bulk download review dialog.

Bridges the analysis workspace ("Download Selected") to the existing V2.0
Phase 2 review pipeline. It composes the *existing* `DownloadWorkflowService`
(filename sanitisation, `validate_final`, `check_duplicate`, `file_category`,
`format_category_label`) and the existing `FileManager` — it does not
reimplement duplicate detection, filename validation, or path safety.

Workflow:
    selected ResourceViews
        -> BulkReviewDialog (filename / destination / status per resource)
        -> accepted (view, filename, destination) entries (READY only)
        -> DownloadService.start_file_download (reviewed flow)
        -> existing QueueController / DownloadManager / Scheduler

The download engine itself is never touched from here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.file_manager import FileManager
from app.core.models import DownloadFile
from app.services.download_workflow import DownloadWorkflowService
from app.services.duplicate_service import (
    DuplicateState,
    duplicate_state_label,
)
from app.services.filename_service import (
    format_file_size,
    sanitize_filename,
)
from app.utils.logger import get_logger

log = get_logger("vanta.ui.bulk_review")


class _ReviewEntry:
    """Per-resource review state backing a single row."""

    def __init__(self, view):
        from app.services.analysis_view import ResourceView

        self.view: ResourceView = view
        self.file: DownloadFile = view.file
        self.filename_edit: Optional[QLineEdit] = None
        self.include_cb: Optional[QCheckBox] = None
        self.status_label: Optional[QLabel] = None

        self.proposed_filename: str = sanitize_filename(view.file.name)
        self.category_label: str = ""
        self.size_label: str = format_file_size(view.file.size)

    @property
    def display_name(self) -> str:
        return self.file.name or "(unnamed)"


class BulkReviewDialog(QDialog):
    """Review a set of selected resources before downloading them.

    Reuses `DownloadWorkflowService` for every validation decision so there
    is a single authoritative source of truth for duplicate / filename /
    destination state.
    """

    def __init__(
        self,
        source_url: str,
        resource_views: list,
        file_manager: FileManager,
        download_dir: str | Path | None = None,
        workflow: DownloadWorkflowService | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Review Downloads")
        self.setMinimumSize(780, 560)

        self._source_url = source_url
        self._file_manager = file_manager
        self._workflow = workflow or DownloadWorkflowService(file_manager)
        self._download_dir = Path(download_dir) if download_dir else file_manager.default_dir

        self._entries: list[_ReviewEntry] = [_ReviewEntry(v) for v in resource_views]
        self.accepted_entries: list[tuple[_ReviewEntry, str, Path]] = []

        self._build_ui()
        self._populate_categories()
        self._build_rows()
        self._recalc_all()
        self._refresh_summary()

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Review Downloads")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Review the filename, destination, and duplicate status for each "
            "selected resource. Only resources marked Ready will be downloaded."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #8A8A9A;")
        layout.addWidget(subtitle)

        self._summary = QLabel()
        self._summary.setObjectName("section_meta")
        layout.addWidget(self._summary)

        # Destination
        dest_frame = self._build_destination_row()
        layout.addWidget(dest_frame)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(8)
        scroll.setWidget(self._rows_container)
        layout.addWidget(scroll, stretch=1)

        button_box = QDialogButtonBox()
        back_btn = QPushButton("Back")
        back_btn.setFixedWidth(120)
        back_btn.clicked.connect(self.reject)

        self._download_btn = QPushButton("Download")
        self._download_btn.setObjectName("primary_action")
        self._download_btn.setFixedWidth(120)
        self._download_btn.clicked.connect(self._on_download)

        button_box.addButton(back_btn, QDialogButtonBox.RejectRole)
        button_box.addButton(self._download_btn, QDialogButtonBox.AcceptRole)
        layout.addWidget(button_box)

    def _build_destination_row(self) -> QWidget:
        frame = QFrame()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._dest_display = QLineEdit()
        self._dest_display.setReadOnly(True)
        self._dest_display.setFixedHeight(32)
        self._dest_display.setText(str(self._download_dir))

        change_btn = QPushButton("Change Folder")
        change_btn.setFixedSize(120, 32)
        change_btn.clicked.connect(self._on_change_folder)

        row.addWidget(QLabel("Destination:"), stretch=0)
        row.addWidget(self._dest_display, stretch=1)
        row.addWidget(change_btn)
        return frame

    def _populate_categories(self):
        for entry in self._entries:
            category = self._workflow.format_category_label(
                self._workflow.file_category(entry.file, entry.file.content_type)
            )
            entry.category_label = category

    def _build_rows(self):
        for entry in self._entries:
            self._rows_layout.addWidget(self._build_entry_row(entry))

    def _build_entry_row(self, entry: _ReviewEntry) -> QWidget:
        frame = QFrame()
        frame.setObjectName("review_row")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        cb = QCheckBox()
        cb.setChecked(True)
        cb.stateChanged.connect(lambda _state, e=entry: self._entry_toggled(e))
        layout.addWidget(cb, alignment=Qt.AlignVCenter)
        entry.include_cb = cb

        name_label = QLabel(entry.display_name)
        name_label.setObjectName("resource_name")
        layout.addWidget(name_label, stretch=1, alignment=Qt.AlignVCenter)

        filename_edit = QLineEdit(entry.proposed_filename)
        filename_edit.setFixedWidth(180)
        filename_edit.textChanged.connect(lambda _t, e=entry: self._on_filename_edited(e))
        layout.addWidget(filename_edit, alignment=Qt.AlignVCenter)
        entry.filename_edit = filename_edit

        cat_label = QLabel(entry.category_label)
        cat_label.setToolTip("Category")
        layout.addWidget(cat_label, alignment=Qt.AlignVCenter)

        size_label = QLabel(entry.size_label)
        size_label.setToolTip("Size")
        layout.addWidget(size_label, alignment=Qt.AlignVCenter)

        url_label = QLabel(entry.file.url or entry.view.final_url or "")
        url_label.setToolTip("Resource URL")
        url_label.setStyleSheet("font-size: 11px; color: #8A8A9A;")
        url_label.setMaximumWidth(200)
        url_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        url_label.setWordWrap(True)
        layout.addWidget(url_label, alignment=Qt.AlignVCenter)

        status_label = QLabel()
        status_label.setMinimumWidth(140)
        layout.addWidget(status_label, alignment=Qt.AlignVCenter)
        entry.status_label = status_label

        return frame

    # ── Evaluation (reuses DownloadWorkflowService) ──────────────────────

    def _evaluate(self, entry: _ReviewEntry) -> tuple[bool, str, DuplicateState]:
        """Return (ready, status_text, status_kind).

        Reuses `validate_final` and `check_duplicate` from the existing
        workflow service. `status_kind` is one of:
        ready / invalid / already_exists / duplicate / conflict / attention
        """
        filename = sanitize_filename(entry.filename_edit.text()) or entry.filename_edit.text()

        valid, msg = self._workflow.validate_final(filename, self._download_dir)
        if not valid:
            kind = "invalid" if not filename else "invalid"
            detail = msg or "Filename or destination is invalid"
            return False, f"{_STATE_LABEL['invalid']} — {detail}", "invalid"

        dup = self._workflow.check_duplicate(entry.file, filename, self._download_dir)
        if dup.state == DuplicateState.READY:
            # Intra-bulk filename collision guard (filename-intelligence domain,
            # not a reimplementation of resource duplicate detection).
            if self._has_filename_conflict(entry, filename):
                return False, "Filename conflict — rename to continue", "conflict"
            return True, _STATE_LABEL["ready"], "ready"
        if dup.state == DuplicateState.ALREADY_EXISTS:
            detail = dup.detail or "File already exists at destination"
            return False, f"{_STATE_LABEL['already_exists']} — {detail}", "already_exists"
        detail = dup.detail or ""
        label = duplicate_state_label(dup.state)
        text = f"{label} — {detail}" if detail else label
        return False, text, "duplicate"

    def _has_filename_conflict(self, entry: _ReviewEntry, filename: str) -> bool:
        target = sanitize_filename(filename)
        if not target:
            return False
        for other in self._entries:
            if other is entry:
                continue
            if other.include_cb.isChecked() and sanitize_filename(other.filename_edit.text()) == target:
                return True
        return False

    def _recalc_all(self):
        for entry in self._entries:
            ready, text, kind = self._evaluate(entry)
            entry.status_label.setText(text)
            entry.status_label.setStyleSheet(_STATE_STYLE.get(kind, _STATE_STYLE["attention"]))

    def _on_filename_edited(self, entry: _ReviewEntry):
        self._recalc_all()
        self._refresh_summary()

    def _entry_toggled(self, entry: _ReviewEntry):
        # Filename-conflict status depends on which siblings are included.
        self._recalc_all()
        self._refresh_summary()

    def _on_change_folder(self):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowTitle("Select Download Location")
        if dialog.exec() == 1:
            selected = dialog.selectedFiles()[0]
            self._download_dir = Path(selected)
            self._dest_display.setText(str(self._download_dir))
            self._recalc_all()
            self._refresh_summary()

    def _ready_count(self) -> int:
        return sum(1 for e in self._entries if self._evaluate(e)[0])

    def _refresh_summary(self):
        total = len(self._entries)
        ready = self._ready_count()
        needs = total - ready
        text = f"{total} resources selected  ·  Ready: {ready}  ·  Needs attention: {needs}"
        self._summary.setText(text)
        self._download_btn.setEnabled(ready > 0)

    def _on_download(self):
        accepted: list[tuple[_ReviewEntry, str, Path]] = []
        for entry in self._entries:
            if not entry.include_cb.isChecked():
                continue
            ready, _text, _kind = self._evaluate(entry)
            if not ready:
                continue
            reviewed_filename = sanitize_filename(entry.filename_edit.text())
            if not reviewed_filename:
                continue
            accepted.append((entry, reviewed_filename, self._download_dir))

        self.accepted_entries = accepted
        self.accept()


_STATE_LABEL = {
    "ready": "Ready",
    "invalid": "Invalid",
    "already_exists": "Already exists",
    "duplicate": "Duplicate",
    "conflict": "Filename conflict",
    "attention": "Needs attention",
}

_STATE_STYLE = {
    "ready": "color: #4CAF50; font-size: 12px;",
    "invalid": "color: #FF6B6B; font-size: 12px;",
    "already_exists": "color: #FFD83D; font-size: 12px;",
    "duplicate": "color: #FF6B6B; font-size: 12px;",
    "conflict": "color: #FFD83D; font-size: 12px;",
    "attention": "color: #FF6B6B; font-size: 12px;",
}

__all__ = ["BulkReviewDialog"]
