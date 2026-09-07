"""V1.8.1 UI Stabilization regression tests.

Covers the V1.8.1 patch:
- Deterministic order in `selected_resources` (matches source list order).
- Selections made out of order (e.g., toggle #2 then #0) still come back
  in the original #0, #1, #2 source order when iterated.
- Bulk operations therefore start downloads in user-facing order.
- `resource_id_for` does not fall back to `id(view)` for objects that
  expose a string `id` attribute.
"""
from dataclasses import dataclass

import pytest

from app.services.selection import (
    ResourceSelectionController,
    resource_id_for,
)


@dataclass
class FakeResource:
    url: str
    name: str

    @property
    def id(self) -> str:
        return f"{self.url}|{self.name}"


@pytest.fixture
def resources() -> list[FakeResource]:
    return [
        FakeResource("https://a.example/file.zip", "file.zip"),
        FakeResource("https://a.example/setup.exe", "setup.exe"),
        FakeResource("https://a.example/readme.txt", "readme.txt"),
    ]


def test_selected_resources_preserves_source_order(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.select_all()

    selected = ctrl.selected_resources(resources)
    assert selected == resources


def test_selected_resources_partial_out_of_order_preserves_source_order(resources):
    """Toggling #2 first, then #0, must still return [r0, r2] (source order)."""
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.toggle(resources[2].id)
    ctrl.toggle(resources[0].id)

    selected = ctrl.selected_resources(resources)
    assert [r.url for r in selected] == [resources[0].url, resources[2].url]


def test_selected_resources_drops_unselected_keeps_order(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.toggle(resources[1].id)

    selected = ctrl.selected_resources(resources)
    assert selected == [resources[1]]


def test_selected_resources_with_reordered_input_list(resources):
    """If the caller passes resources in a different order, the output
    follows that order, not insertion order into the set."""
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.select_all()

    reordered = [resources[2], resources[0], resources[1]]
    selected = ctrl.selected_resources(reordered)
    assert selected == reordered


def test_resource_id_for_uses_string_id_attribute():
    class WithId:
        def __init__(self, rid: str):
            self.id = rid

    obj = WithId("custom-identifier")
    assert resource_id_for(obj) == "custom-identifier"


def test_resource_id_for_falls_back_to_url_when_no_string_id():
    class NoId:
        def __init__(self, url: str):
            self.url = url

    obj = NoId("https://example.com/x.bin")
    assert resource_id_for(obj) == "https://example.com/x.bin"
