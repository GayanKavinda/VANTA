"""Pure resource grouping for V1.9.

Grouping answers the question *"how should the available resources be
presented in the UI?"* It is intentionally separate from filtering,
sorting, categorization, and selection.

Hard rules:
  * Pure Python — no PySide6, no I/O, no resolver/probe calls.
  * Never mutates the input list.
  * Never reorders resources within a group (grouping is a partition,
    not a sort).
  * Every input resource appears in exactly one output group; none are
    lost, none are duplicated.
  * Returns references to the original `ResourceView` instances.
  * Reads category, score, and confidence to assign groups but does not
    mutate them.

Group assignments (defaults):
  * MAIN     — INSTALLER (high/medium confidence), ARCHIVE, PART
  * OPTIONAL — PATCH, INSTALLER (low confidence), UNKNOWN
               with HIGH confidence and score >= 60
  * OTHER    — DOCUMENTATION, remaining UNKNOWN resources,
               REJECTED confidence
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from app.core.models import ConfidenceLevel
from app.services.analysis_view import ResourceView
from app.services.categorization import ResourceCategory, categorize_resource


class ResourceGroup(str, Enum):
    MAIN = "main"
    OPTIONAL = "optional"
    OTHER = "other"


@dataclass(frozen=True)
class GroupedResources:
    main: list[ResourceView]
    optional: list[ResourceView]
    other: list[ResourceView]

    def total(self) -> int:
        return len(self.main) + len(self.optional) + len(self.other)

    def by_group(self) -> dict[ResourceGroup, list[ResourceView]]:
        return {
            ResourceGroup.MAIN: list(self.main),
            ResourceGroup.OPTIONAL: list(self.optional),
            ResourceGroup.OTHER: list(self.other),
        }


def _assign_group(view: ResourceView, category: ResourceCategory) -> ResourceGroup:
    if view.confidence == ConfidenceLevel.REJECTED:
        return ResourceGroup.OTHER

    if category is ResourceCategory.PART:
        return ResourceGroup.MAIN
    if category is ResourceCategory.ARCHIVE:
        return ResourceGroup.MAIN
    if category is ResourceCategory.INSTALLER:
        if view.confidence == ConfidenceLevel.HIGH:
            return ResourceGroup.MAIN
        if view.confidence == ConfidenceLevel.MEDIUM:
            return ResourceGroup.MAIN
        if view.confidence == ConfidenceLevel.LOW:
            return ResourceGroup.OPTIONAL
        return ResourceGroup.OPTIONAL
    if category is ResourceCategory.PATCH:
        return ResourceGroup.OPTIONAL
    if category is ResourceCategory.DOCUMENTATION:
        return ResourceGroup.OTHER
    if category is ResourceCategory.UNKNOWN:
        if view.score >= 60 and view.confidence == ConfidenceLevel.HIGH:
            return ResourceGroup.OPTIONAL
        return ResourceGroup.OTHER
    return ResourceGroup.OTHER


def group_resources(resources: Iterable[ResourceView]) -> GroupedResources:
    """Partition `resources` into MAIN / OPTIONAL / OTHER groups.

    Order within each group follows input order. Every input resource
    appears in exactly one output group.
    """
    main: list[ResourceView] = []
    optional: list[ResourceView] = []
    other: list[ResourceView] = []

    for view in resources:
        category = categorize_resource(view)
        group = _assign_group(view, category)
        if group is ResourceGroup.MAIN:
            main.append(view)
        elif group is ResourceGroup.OPTIONAL:
            optional.append(view)
        else:
            other.append(view)

    return GroupedResources(main=main, optional=optional, other=other)
