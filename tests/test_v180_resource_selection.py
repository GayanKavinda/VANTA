"""V1.8 Resource Selection tests.

Validates the pure selection controller used by the HomePage. Selection is a
UI-only concern and is intentionally kept out of the resolver, the probe, and
the DownloadFile model.
"""
from dataclasses import dataclass

import pytest

from app.services.selection import (
    ResourceSelectionController,
    SelectionState,
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


def test_initial_state_is_empty():
    ctrl = ResourceSelectionController()
    assert isinstance(ctrl.state, SelectionState)
    assert ctrl.state.count == 0
    assert ctrl.state.eligible_count == 0
    assert not ctrl.state.any_selected
    assert not ctrl.state.all_eligible_selected


def test_set_eligible_clears_stale_selections(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.toggle(resources[0].id)
    assert ctrl.is_selected(resources[0].id)

    ctrl.set_eligible([resources[1].id])

    assert ctrl.state.eligible_count == 1
    assert ctrl.state.count == 0
    assert not ctrl.is_selected(resources[0].id)


def test_toggle_adds_and_removes(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])

    ctrl.toggle(resources[0].id)
    assert ctrl.is_selected(resources[0].id)
    assert ctrl.state.count == 1
    assert ctrl.state.any_selected

    ctrl.toggle(resources[0].id)
    assert not ctrl.is_selected(resources[0].id)
    assert ctrl.state.count == 0


def test_toggle_ignores_non_eligible_id(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.toggle("not-eligible-id")
    assert ctrl.state.count == 0


def test_select_all_and_deselect_all(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])

    ctrl.select_all()
    assert ctrl.state.count == 3
    assert ctrl.state.all_eligible_selected

    ctrl.deselect_all()
    assert ctrl.state.count == 0
    assert not ctrl.state.any_selected


def test_selected_resources_returns_only_chosen(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.toggle(resources[0].id)
    ctrl.toggle(resources[2].id)

    selected = ctrl.selected_resources(resources)
    assert [r.url for r in selected] == [resources[0].url, resources[2].url]


def test_resource_id_for_resource_view():
    from app.core.models import DownloadFile
    from app.services.analysis_view import ResourceView

    df = DownloadFile(name="zip", url="https://x/y.zip")
    rv = ResourceView(
        file=df,
        score=90,
        confidence="high",
        reasons=[],
    )
    assert resource_id_for(rv) == "https://x/y.zip|zip"


def test_all_eligible_selected_only_when_all_checked(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    assert not ctrl.state.all_eligible_selected

    ctrl.toggle(resources[0].id)
    ctrl.toggle(resources[1].id)
    assert not ctrl.state.all_eligible_selected

    ctrl.toggle(resources[2].id)
    assert ctrl.state.all_eligible_selected


def test_selected_resources_drops_missing_ids(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])
    ctrl.select_all()

    only_first = [resources[0]]
    selected = ctrl.selected_resources(only_first)
    assert selected == [resources[0]]


def test_repeated_toggle_alternates_selection_state(resources):
    ctrl = ResourceSelectionController()
    ctrl.set_eligible([r.id for r in resources])

    for _ in range(5):
        ctrl.toggle(resources[0].id)
    assert ctrl.is_selected(resources[0].id)

    for _ in range(5):
        ctrl.toggle(resources[0].id)
    assert not ctrl.is_selected(resources[0].id)
