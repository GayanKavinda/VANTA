from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.core.validators import is_valid_url
from app.services.analysis_view import (
    AnalysisViewModel,
    ResourceView,
)
from app.services.categorization import categorize_resource
from app.services.filtering import (
    FilterMode,
    ResourceFilter,
    apply_filter,
)
from app.services.grouping import (
    ResourceGroup,
    group_resources,
)
from app.services.selection import (
    ResourceSelectionController,
    SelectionState,
    resource_id_for,
)
from app.services.sorting import (
    SortMode,
    SortSpec,
    sort_resources,
)
from app.utils.logger import get_logger

log = get_logger("vanta.ui.home")


class HomePage(QWidget):
    url_analyzed = Signal(str)
    download_requested = Signal(str, object)
    downloads_selected = Signal(str, list)

    def __init__(self):
        super().__init__()

        self._current_view: AnalysisViewModel | None = None
        self._current_source_url: str | None = None
        self._selection = ResourceSelectionController()
        self._analyzing: bool = False

        header = HeaderWidget()

        url_row = self._build_url_row()
        url_container = QWidget()
        url_container.setLayout(url_row)

        self._result_area = ResultArea()
        self._result_area.download_clicked.connect(self._on_resource_download)
        self._result_area.selection_changed.connect(self._on_selection_changed)
        self._result_area.download_selected_clicked.connect(self._on_download_selected)
        self._result_area.select_all_clicked.connect(self._on_select_all)
        self._result_area.deselect_all_clicked.connect(self._on_deselect_all)
        self._result_area.select_visible_clicked.connect(self._on_select_visible)
        self._result_area.deselect_visible_clicked.connect(self._on_deselect_visible)
        self._result_area.details_requested.connect(self._on_details_requested)
        self._result_area.filter_changed.connect(self._on_filter_changed)
        self._result_area.sort_changed.connect(self._on_sort_changed)

        footer = FooterWidget()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(24)
        layout.addWidget(header)
        layout.addWidget(url_container)
        layout.addWidget(self._result_area)
        layout.addWidget(footer)
        layout.addStretch(1)

    def _build_url_row(self) -> QHBoxLayout:
        url_row = QHBoxLayout()
        url_row.setSpacing(12)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText(
            "Paste a page URL (e.g. https://example.com/game-page)"
        )
        self._url_input.setFixedHeight(48)
        self._url_input.returnPressed.connect(self._on_analyze)
        url_row.addWidget(self._url_input, stretch=1)

        self._analyze_btn = QPushButton("Analyze")
        self._analyze_btn.setObjectName("primary_action")
        self._analyze_btn.setFixedSize(120, 48)
        self._analyze_btn.clicked.connect(self._on_analyze)
        url_row.addWidget(self._analyze_btn)

        return url_row

    def set_current_url_for_result(self, url: str):
        self._current_source_url = url
        self._result_area.set_current_url(url)

    def set_analyzing(self, analyzing: bool):
        self._analyzing = analyzing
        self._analyze_btn.setEnabled(not analyzing)
        self._url_input.setEnabled(not analyzing)
        if analyzing:
            self._result_area.show_loading()
        else:
            self._analyze_btn.setText("Analyze")

    def _on_analyze(self):
        if self._analyzing:
            return
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
        self._selection.deselect_all()
        self.set_analyzing(True)
        self.url_analyzed.emit(url)

    def _on_resource_download(self, view: ResourceView):
        if not self._current_source_url:
            log.warning("Download requested without a known source URL")
            return
        self.download_requested.emit(self._current_source_url, view)

    def _on_selection_changed(self, resource_id: str, selected: bool):
        if selected:
            self._selection.toggle(resource_id)
        else:
            if self._selection.is_selected(resource_id):
                self._selection.toggle(resource_id)
        self._result_area.refresh_selection_toolbar(self._selection.state)

    def _on_select_all(self):
        self._selection.select_all()
        self._result_area.refresh_selection_toolbar(self._selection.state)
        self._result_area.refresh_row_checkboxes(self._selection)

    def _on_deselect_all(self):
        self._selection.deselect_all()
        self._result_area.refresh_selection_toolbar(self._selection.state)
        self._result_area.refresh_row_checkboxes(self._selection)

    def _on_select_visible(self):
        if not self._current_view:
            return
        visible = self._result_area._visible_resource_ids()
        self._selection.select_visible(visible)
        self._result_area.refresh_selection_toolbar(self._selection.state)
        self._result_area.refresh_row_checkboxes(self._selection)

    def _on_deselect_visible(self):
        if not self._current_view:
            return
        visible = self._result_area._visible_resource_ids()
        self._selection.deselect_visible(visible)
        self._result_area.refresh_selection_toolbar(self._selection.state)
        self._result_area.refresh_row_checkboxes(self._selection)

    def _on_filter_changed(self, filter_spec: ResourceFilter):
        if not self._current_view:
            return
        self._result_area.refresh_resources(
            self._current_view,
            self._selection,
        )
        self._result_area.refresh_selection_toolbar(
            self._selection.state
        )

    def _on_sort_changed(self, sort_spec: SortSpec):
        if not self._current_view:
            return
        self._result_area.refresh_resources(
            self._current_view,
            self._selection,
        )
        self._result_area.refresh_selection_toolbar(
            self._selection.state
        )

    def _on_download_selected(self):
        if not self._current_source_url or not self._current_view:
            return
        selected = self._selection.selected_resources(self._current_view.resources)
        if not selected:
            self._result_area.show_selection_warning(
                "Select at least one resource to download."
            )
            return
        self._result_area.clear_selection_warning()
        self.downloads_selected.emit(self._current_source_url, selected)

    def _on_details_requested(self, view: ResourceView):
        dialog = ResourceDetailsDialog(view, self)
        dialog.exec()

    def show_analysis_view(self, view_model: AnalysisViewModel):
        self._current_view = view_model
        self.set_analyzing(False)
        self._result_area.reset_controls()
        ids = [resource_id_for(rv) for rv in view_model.resources]
        self._selection.set_eligible(ids)
        self._selection.deselect_all()
        if view_model.is_error:
            self._result_area.show_error(
                view_model.title or "Analysis failed",
                "VANTA could not complete the analysis. "
                "Check the URL or try again.",
            )
        elif view_model.is_unsupported or not view_model.has_resources:
            self._result_area.show_unsupported(
                view_model.title or "No downloadable resources found",
                "No downloadable resources were discovered on this page.",
            )
        else:
            self._result_area.show_view_model(view_model, self._selection)

    def show_error(self, title: str, message: str):
        self.set_analyzing(False)
        self._result_area.show_error(title, message)

    def clear_result(self):
        self._current_view = None
        self._selection.set_eligible([])
        self._selection.deselect_all()
        self._result_area.reset_controls()
        self._result_area.clear()


class HeaderWidget(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("VANTA")
        title.setStyleSheet("font-size: 28px; font-weight: 700; letter-spacing: 0.5px;")

        subtitle = QLabel("Analyze a page, review the discovered resources, and download what you need.")
        subtitle.setStyleSheet("font-size: 14px; color: #8A8A9A;")

        layout.addWidget(title)
        layout.addWidget(subtitle)


class ResultArea(QWidget):
    download_clicked = Signal(object)
    selection_changed = Signal(str, bool)
    download_selected_clicked = Signal()
    select_all_clicked = Signal()
    deselect_all_clicked = Signal()
    select_visible_clicked = Signal()
    deselect_visible_clicked = Signal()
    details_requested = Signal(object)
    filter_changed = Signal(object)
    sort_changed = Signal(object)

    def __init__(self):
        super().__init__()

        self._current_url: str | None = None
        self._filter_spec: ResourceFilter = ResourceFilter(FilterMode.ALL)
        self._sort_spec: SortSpec = SortSpec(SortMode.RECOMMENDED)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)

        self._loading = self._build_loading()
        self._layout.addWidget(self._loading)
        self._loading.hide()

        self._error = self._build_error_state()
        self._layout.addWidget(self._error)
        self._error.hide()

        self._unsupported = self._build_unsupported_state()
        self._layout.addWidget(self._unsupported)
        self._unsupported.hide()

        self._selection_warning = QLabel()
        self._selection_warning.setStyleSheet("color: #FFD83D; font-size: 12px;")
        self._selection_warning.hide()
        self._layout.addWidget(self._selection_warning)

        self._section_title = QLabel()
        self._section_title.setObjectName("section_title")
        self._section_title.hide()
        self._layout.addWidget(self._section_title)

        self._control_bar = self._build_control_bar()
        self._layout.addWidget(self._control_bar)
        self._control_bar.show()

        self._selection_toolbar = self._build_selection_toolbar()
        self._layout.addWidget(self._selection_toolbar)
        self._selection_toolbar.hide()

        self._resource_list = QListWidget()
        self._resource_list.setObjectName("resource_list")
        self._resource_list.setSpacing(8)
        self._resource_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._resource_list.setStyleSheet(
            "QListWidget#resource_list { background: transparent; border: none; }"
            "QListWidget#resource_list::item { border: none; background: transparent; }"
        )
        self._resource_list.hide()
        self._layout.addWidget(self._resource_list)

        self._layout.addStretch(1)

        self._resource_rows: list[ResourceRow] = []

    def _build_loading(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("empty_state")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        spinner_row = QHBoxLayout()
        spinner_row.setSpacing(12)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        spinner_row.addWidget(bar, stretch=1)

        layout.addLayout(spinner_row)

        title = QLabel("Analyzing the page...")
        title.setObjectName("empty_state_title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        body = QLabel(
            "Resolving the source page, scanning for downloadable links, "
            "and classifying resources. This may take a few seconds."
        )
        body.setObjectName("empty_state_body")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(body)

        return frame

    def _build_error_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("error_state")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        title = QLabel()
        title.setObjectName("error_state_title")
        layout.addWidget(title)

        body = QLabel()
        body.setObjectName("error_state_body")
        body.setWordWrap(True)
        layout.addWidget(body)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._open_btn = QPushButton("Open in Browser")
        self._open_btn.setObjectName("secondary")
        self._open_btn.setFixedHeight(34)
        self._open_btn.clicked.connect(self._open_in_browser)
        actions.addWidget(self._open_btn)

        self._copy_btn = QPushButton("Copy URL")
        self._copy_btn.setObjectName("secondary")
        self._copy_btn.setFixedHeight(34)
        self._copy_btn.clicked.connect(self._copy_url)
        actions.addWidget(self._copy_btn)

        actions.addStretch(1)
        layout.addLayout(actions)

        self._error_title = title
        self._error_body = body

        return frame

    def _build_unsupported_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("empty_state")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        title = QLabel()
        title.setObjectName("empty_state_title")
        layout.addWidget(title)

        body = QLabel()
        body.setObjectName("empty_state_body")
        body.setWordWrap(True)
        layout.addWidget(body)

        self._unsupported_title = title
        self._unsupported_body = body

        return frame

    def _build_control_bar(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        filter_label = QLabel("Filter:")
        filter_label.setObjectName("section_meta")
        layout.addWidget(filter_label)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(
            [
                "All",
                "High",
                "Medium",
                "Low",
                "File Type",
            ]
        )
        self._filter_combo.setFixedWidth(140)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_index_changed)
        layout.addWidget(self._filter_combo)

        self._file_type_combo = QComboBox()
        self._file_type_combo.addItems(
            ["zip", "rar", "7z", "tar", "gz", "exe", "msi", "pdf", "txt", "iso", "bin"]
        )
        self._file_type_combo.setFixedWidth(130)
        self._file_type_combo.currentIndexChanged.connect(self._on_file_type_changed)
        self._file_type_combo.setEnabled(False)
        layout.addWidget(self._file_type_combo)

        sort_label = QLabel("Sort:")
        sort_label.setObjectName("section_meta")
        layout.addWidget(sort_label)

        self._sort_combo = QComboBox()
        self._sort_combo.addItems(
            [
                "Recommended",
                "Score ↑",
                "Score ↓",
                "Size ↑",
                "Size ↓",
                "Filename ↑",
                "Filename ↓",
            ]
        )
        self._sort_combo.setFixedWidth(170)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_index_changed)
        layout.addWidget(self._sort_combo)

        layout.addStretch(1)

        self._select_visible_btn = QPushButton("Select Visible")
        self._select_visible_btn.setObjectName("secondary")
        self._select_visible_btn.setProperty("size", "small")
        self._select_visible_btn.clicked.connect(self.select_visible_clicked.emit)
        layout.addWidget(self._select_visible_btn)

        self._deselect_visible_btn = QPushButton("Deselect Visible")
        self._deselect_visible_btn.setObjectName("secondary")
        self._deselect_visible_btn.setProperty("size", "small")
        self._deselect_visible_btn.clicked.connect(self.deselect_visible_clicked.emit)
        layout.addWidget(self._deselect_visible_btn)

        return frame

    def _build_selection_toolbar(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.setObjectName("secondary")
        self._select_all_btn.setProperty("size", "small")
        self._select_all_btn.clicked.connect(self.select_all_clicked.emit)
        layout.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("Deselect All")
        self._deselect_all_btn.setObjectName("secondary")
        self._deselect_all_btn.setProperty("size", "small")
        self._deselect_all_btn.clicked.connect(self.deselect_all_clicked.emit)
        layout.addWidget(self._deselect_all_btn)

        self._selection_summary = QLabel()
        self._selection_summary.setObjectName("section_meta")
        layout.addWidget(self._selection_summary)

        layout.addStretch(1)

        self._download_selected_btn = QPushButton("Download Selected")
        self._download_selected_btn.setObjectName("primary_action")
        self._download_selected_btn.setFixedHeight(36)
        self._download_selected_btn.clicked.connect(self.download_selected_clicked.emit)
        layout.addWidget(self._download_selected_btn)

        return frame

    def _on_filter_index_changed(self, index: int):
        mode_map = {
            0: FilterMode.ALL,
            1: FilterMode.HIGH,
            2: FilterMode.MEDIUM,
            3: FilterMode.LOW,
            4: FilterMode.FILE_TYPE,
        }
        mode = mode_map.get(index, FilterMode.ALL)
        if mode is FilterMode.FILE_TYPE:
            self._file_type_combo.setEnabled(True)
            file_type = self._file_type_combo.currentText()
            try:
                self._filter_spec = ResourceFilter(FilterMode.FILE_TYPE, file_type=file_type)
            except ValueError:
                self._filter_spec = ResourceFilter(FilterMode.ALL)
        else:
            self._file_type_combo.setEnabled(False)
            try:
                self._filter_spec = ResourceFilter(mode)
            except ValueError:
                self._filter_spec = ResourceFilter(FilterMode.ALL)
        self.filter_changed.emit(self._filter_spec)

    def _on_file_type_changed(self, index: int):
        if self._filter_spec.mode is FilterMode.FILE_TYPE:
            file_type = self._file_type_combo.itemText(index)
            try:
                self._filter_spec = ResourceFilter(FilterMode.FILE_TYPE, file_type=file_type)
            except ValueError:
                self._filter_spec = ResourceFilter(FilterMode.ALL)
            self.filter_changed.emit(self._filter_spec)

    def _on_sort_index_changed(self, index: int):
        sort_map = {
            0: SortMode.RECOMMENDED,
            1: SortMode.SCORE_ASC,
            2: SortMode.SCORE_DESC,
            3: SortMode.SIZE_ASC,
            4: SortMode.SIZE_DESC,
            5: SortMode.FILENAME_ASC,
            6: SortMode.FILENAME_DESC,
        }
        mode = sort_map.get(index, SortMode.RECOMMENDED)
        try:
            self._sort_spec = SortSpec(mode)
        except TypeError:
            self._sort_spec = SortSpec(SortMode.RECOMMENDED)
        self.sort_changed.emit(self._sort_spec)

    def clear(self):
        self._loading.hide()
        self._error.hide()
        self._unsupported.hide()
        self._selection_warning.hide()
        self._section_title.hide()
        self._selection_toolbar.hide()
        self._resource_list.hide()
        self._resource_list.clear()
        self._resource_rows = []
        self._current_url = None

    def show_loading(self):
        self.clear()
        self._loading.show()

    def show_view_model(self, vm: AnalysisViewModel, selection: ResourceSelectionController):
        self.clear()
        title_text = (
            f"Discovered {len(vm.resources)} downloadable resource(s) — "
            f"{vm.title or 'Source page'}"
        )
        self._section_title.setText(title_text)
        self._section_title.show()
        self._render_resources(vm, selection)

    def refresh_resources(
        self,
        vm: AnalysisViewModel,
        selection: ResourceSelectionController,
    ):
        self._render_resources(vm, selection)

    def reset_controls(self):
        self._filter_spec = ResourceFilter(FilterMode.ALL)
        self._sort_spec = SortSpec(SortMode.RECOMMENDED)

        self._filter_combo.blockSignals(True)
        self._sort_combo.blockSignals(True)

        self._filter_combo.setCurrentIndex(0)
        self._sort_combo.setCurrentIndex(0)

        self._filter_combo.blockSignals(False)
        self._sort_combo.blockSignals(False)

        self._file_type_combo.setEnabled(False)

    def _render_resources(
        self,
        vm: AnalysisViewModel,
        selection: ResourceSelectionController,
    ):
        # V1.9 pipeline: Filter → Sort → Group (presentation only).
        filtered = apply_filter(vm.resources, self._filter_spec)
        sorted_view = sort_resources(filtered, self._sort_spec)
        grouped = group_resources(sorted_view)

        self._resource_list.clear()
        self._resource_rows = []

        for group, label in (
            (grouped.main, "Main"),
            (grouped.optional, "Optional"),
            (grouped.other, "Other"),
        ):
            if not group:
                continue
            header_item = QListWidgetItem(self._resource_list)
            header_item.setData(Qt.UserRole, "group_header")
            header_item.setSizeHint(QSize(0, 22))
            self._resource_list.addItem(header_item)
            header_label = QLabel(label.upper())
            header_label.setObjectName("section_meta")
            self._resource_list.setItemWidget(header_item, header_label)

            for rv in group:
                item = QListWidgetItem(self._resource_list)
                row = ResourceRow(rv)
                item.setSizeHint(row.sizeHint())
                self._resource_list.addItem(item)
                self._resource_list.setItemWidget(item, row)

                row.download_clicked.connect(lambda checked=False, r=rv: self.download_clicked.emit(r))
                row.selection_changed.connect(self.selection_changed.emit)
                row.details_clicked.connect(self.details_requested.emit)

                self._resource_rows.append(row)

        self._resource_list.show()
        self._control_bar.show()
        self._selection_toolbar.show()
        self.refresh_selection_toolbar(selection.state)
        self.refresh_row_checkboxes(selection)

    def show_unsupported(self, title_text: str, body_text: str):
        self.clear()
        self._unsupported_title.setText(title_text)
        self._unsupported_body.setText(body_text)
        self._unsupported.show()

    def show_error(self, title_text: str, body_text: str):
        self.clear()
        self._error_title.setText(title_text)
        if body_text:
            self._error_body.setText(body_text)
            self._error_body.show()
        else:
            self._error_body.hide()
        self._error.show()

    def show_selection_warning(self, message: str):
        self._selection_warning.setText(message)
        self._selection_warning.show()

    def clear_selection_warning(self):
        self._selection_warning.hide()

    def refresh_selection_toolbar(self, state: SelectionState):
        if not self._selection_toolbar.isVisible():
            return
        self._selection_summary.setText(
            f"{state.count} of {state.eligible_count} selected"
        )
        self._select_all_btn.setEnabled(state.eligible_count > 0 and not state.all_eligible_selected)
        self._deselect_all_btn.setEnabled(state.any_selected)
        self._download_selected_btn.setEnabled(state.any_selected)

    def refresh_row_checkboxes(self, selection: ResourceSelectionController):
        for row in self._resource_rows:
            row.set_checked(selection.is_selected(row.resource_id))

    def _visible_resource_ids(self) -> list[str]:
        """Return the resource IDs currently visible in the list.

        Used by `Select Visible` / `Deselect Visible`. The list is
        populated by `show_view_model`, which applies the current
        filter/sort/group pipeline.
        """
        return [row.resource_id for row in self._resource_rows]

    def set_current_url(self, url: str):
        self._current_url = url

    def _open_in_browser(self):
        if self._current_url:
            import webbrowser
            webbrowser.open(self._current_url)

    def _copy_url(self):
        if self._current_url:
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(self._current_url)
            log.info("URL copied to clipboard")


class ResourceRow(QFrame):
    download_clicked = Signal()
    selection_changed = Signal(str, bool)
    details_clicked = Signal()

    def __init__(self, view: ResourceView):
        super().__init__()
        self._view = view
        self._resource_id = resource_id_for(view)

        self.setObjectName("resource_row")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(12)

        self._checkbox = QCheckBox()
        self._checkbox.setProperty("resourceId", self._resource_id)
        self._checkbox.toggled.connect(self._on_checkbox_toggled)
        outer.addWidget(self._checkbox, alignment=Qt.AlignVCenter)

        body = QVBoxLayout()
        body.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        name_label = QLabel(view.file.name or "(unnamed)")
        name_label.setObjectName("resource_name")
        name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top_row.addWidget(name_label, stretch=1)

        self._badge = QLabel(view.confidence_label)
        badge_obj = f"confidence_{view.confidence}"
        self._badge.setObjectName(badge_obj)
        top_row.addWidget(self._badge)

        size_text = view.size_label or "Unknown size"
        size_label = QLabel(size_text)
        size_label.setObjectName("resource_size")
        top_row.addWidget(size_label)

        body.addLayout(top_row)

        meta_parts = []
        if view.type_label:
            meta_parts.append(view.type_label)
        reasons_text = " · ".join(view.reasons[:3]) if view.reasons else f"Score {view.score}"
        if reasons_text:
            meta_parts.append(reasons_text)

        meta_label = QLabel(" · ".join(meta_parts))
        meta_label.setObjectName("resource_meta")
        meta_label.setWordWrap(True)
        body.addWidget(meta_label)

        outer.addLayout(body, stretch=1)

        actions = QVBoxLayout()
        actions.setSpacing(6)

        download_btn = QPushButton("Download")
        download_btn.setObjectName("primary_action")
        download_btn.setProperty("size", "small")
        download_btn.setFixedWidth(110)
        download_btn.clicked.connect(self.download_clicked.emit)
        actions.addWidget(download_btn)

        details_btn = QPushButton("Details")
        details_btn.setObjectName("secondary")
        details_btn.setProperty("size", "small")
        details_btn.setFixedWidth(110)
        details_btn.clicked.connect(self.details_clicked.emit)
        actions.addWidget(details_btn)

        outer.addLayout(actions)

    @property
    def resource_id(self) -> str:
        return self._resource_id

    def set_checked(self, checked: bool):
        if self._checkbox.isChecked() != checked:
            self._checkbox.blockSignals(True)
            self._checkbox.setChecked(checked)
            self._checkbox.blockSignals(False)

    def _on_checkbox_toggled(self, checked: bool):
        self.selection_changed.emit(self._resource_id, checked)


class ResourceDetailsDialog(QDialog):
    def __init__(self, view: ResourceView, parent=None):
        super().__init__(parent)
        self.setObjectName("resource_details_dialog")
        self.setWindowTitle(f"Resource Details — {view.file.name or 'unnamed'}")
        self.setMinimumSize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(view.file.name or "(unnamed)")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        body.setHtml(self._render(view))
        layout.addWidget(body, stretch=1)

        copy_btn = QPushButton("Copy Resource URL")
        copy_btn.setObjectName("secondary")
        copy_btn.clicked.connect(lambda: self._copy_url(view.file.url))

        button_box = QDialogButtonBox()
        button_box.addButton(copy_btn, QDialogButtonBox.ActionRole)
        button_box.addButton(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        layout.addWidget(button_box)

    @staticmethod
    def _render(view: ResourceView) -> str:
        from html import escape

        rows = [
            ("Filename", view.file.name or "(unnamed)"),
            ("Confidence", f"{view.confidence_label} (score {view.score})"),
            ("Size", view.size_label or "Unknown"),
            ("Content-Type", view.type_label or "Unknown"),
            ("URL", view.file.url),
        ]
        body_html = "".join(
            f"<tr><th align='left'>{escape(k)}</th><td>{escape(v)}</td></tr>"
            for k, v in rows
        )
        reasons_html = "".join(
            f"<li>{escape(r)}</li>" for r in view.reasons
        ) or "<li><i>No resolution reasons available.</i></li>"

        return f"""
        <style>
          table {{ border-collapse: collapse; }}
          th, td {{ padding: 6px 10px; border-bottom: 1px solid #2A2C36; }}
          th {{ color: #8A8A9A; font-weight: 600; width: 30%; }}
        </style>
        <table>{body_html}</table>
        <p style="color:#8A8A9A; margin-top:12px;">Resolution reasons</p>
        <ul>{reasons_html}</ul>
        """

    def _copy_url(self, url: str):
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(url)
        log.info("Resource URL copied to clipboard")


class FooterWidget(QWidget):
    def __init__(self):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel("Recent Activity")
        label.setObjectName("section_title")
        layout.addWidget(label)

        layout.addStretch(1)

        status = QLabel("No recent downloads")
        status.setObjectName("section_meta")
        layout.addWidget(status)