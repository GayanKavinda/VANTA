"""Pure selection controller for analysis resources.

Selection is a UI-only concern. This controller is intentionally free of Qt
imports so it can be tested independently and so the resolver remains
unaware of selection.
"""
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class SelectionState:
    selected_ids: set[str] = field(default_factory=set)
    eligible_ids: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.selected_ids)

    @property
    def eligible_count(self) -> int:
        return len(self.eligible_ids)

    @property
    def all_eligible_selected(self) -> bool:
        return bool(self.eligible_ids) and self.selected_ids >= set(self.eligible_ids)

    @property
    def any_selected(self) -> bool:
        return bool(self.selected_ids)


class ResourceSelectionController:

    def __init__(self):
        self._state = SelectionState()

    @property
    def state(self) -> SelectionState:
        return self._state

    def set_eligible(self, ids: Iterable[str]):
        eligible = list(ids)
        self._state.eligible_ids = eligible
        self._state.selected_ids = {
            sid for sid in self._state.selected_ids if sid in eligible
        }

    def is_selected(self, resource_or_id) -> bool:
        """Return whether the given resource (or resource id) is selected.

        Accepts either a `ResourceView`/`DownloadFile`-like object or a
        raw id string. This is purely a convenience for call sites that
        already have the resource object in hand; the underlying storage
        is still a set of string ids.
        """
        if isinstance(resource_or_id, str):
            return resource_or_id in self._state.selected_ids
        return resource_id_for(resource_or_id) in self._state.selected_ids

    def toggle(self, resource_or_id):
        """Toggle selection for the given resource (or resource id)."""
        if isinstance(resource_or_id, str):
            rid = resource_or_id
        else:
            rid = resource_id_for(resource_or_id)
        if rid not in self._state.eligible_ids:
            return
        if rid in self._state.selected_ids:
            self._state.selected_ids.discard(rid)
        else:
            self._state.selected_ids.add(rid)

    def select_all(self):
        self._state.selected_ids = set(self._state.eligible_ids)

    def deselect_all(self):
        self._state.selected_ids.clear()

    def select_visible(self, visible_ids: Iterable[str]):
        """Select every id in `visible_ids` that is currently eligible.

        Hidden selections (those not in `visible_ids` but still eligible) are
        preserved. Filtering only changes visibility; this method only
        adds to the selection set, never removes.
        """
        for vid in visible_ids:
            if vid in self._state.eligible_ids:
                self._state.selected_ids.add(vid)

    def deselect_visible(self, visible_ids: Iterable[str]):
        """Deselect every id in `visible_ids` that is currently selected.

        Hidden selections (those not in `visible_ids`) are preserved. This
        method only removes from the selection set, never adds.
        """
        for vid in visible_ids:
            if vid in self._state.selected_ids:
                self._state.selected_ids.discard(vid)

    def visible_selection_count(self, visible_ids: Iterable[str]) -> int:
        """Return the number of currently visible resources that are selected."""
        visible_set = set(visible_ids)
        return sum(1 for vid in self._state.selected_ids if vid in visible_set)

    def selected_resources(self, resources: list) -> list:
        """Return selected resources in the order they appear in `resources`.

        Iterates the source list rather than the selection set so that bulk
        operations start downloads in a deterministic, user-friendly order.
        """
        return [
            resource
            for resource in resources
            if resource_id_for(resource) in self._state.selected_ids
        ]


def resource_id_for(view) -> str:
    from app.services.analysis_view import ResourceView
    if isinstance(view, ResourceView):
        return f"{view.file.url}|{view.file.name}"
    if isinstance(view, dict):
        return str(view.get("id", view.get("url", "")))
    rid = getattr(view, "id", None)
    if isinstance(rid, str):
        return rid
    return str(getattr(view, "url", id(view)))