"""Pure resource-filtering model for V1.9.

Filtering answers the question *"which resources should be visible right
now?"* It is intentionally:

* pure Python (no PySide6, no I/O)
* unaware of selection state
* non-mutating (input list and resource objects are never modified)
* order-preserving (output follows the input list order)

Hard boundary: this module MUST NOT import from
`app.services.resolver`, `app.services.resource_probe`,
`app.services.analyzer`, `app.services.selection`, or
`app.core.downloader`. The selection layer is composed *downstream* of
filtering, not the other way around.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from app.core.models import ConfidenceLevel
from app.services.analysis_view import ResourceView


class FilterMode(str, Enum):
    """Supported resource filters.

    `FILE_TYPE` is parameterized by `ResourceFilter.file_type`.
    Any unknown / unrecognised mode is rejected; callers should not pass
    arbitrary strings. This keeps the API explicit and prevents silent
    fallbacks.
    """

    ALL = "all"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    FILE_TYPE = "file_type"


@dataclass(frozen=True)
class ResourceFilter:
    """Filter specification.

    `mode` is the primary discriminator. For `FILE_TYPE` filters, callers
    must also supply `file_type` (case-insensitive, no leading dot).
    Construction validates invariants so we never construct a malformed
    filter (e.g. `FILE_TYPE` without a file type).
    """

    mode: FilterMode = FilterMode.ALL
    file_type: Optional[str] = None

    def __post_init__(self):
        if self.file_type is not None:
            normalized = self.file_type.strip().lstrip(".").lower()
            if not normalized:
                raise ValueError("file_type must be a non-empty string when provided")
            if any(ch.isspace() for ch in normalized):
                raise ValueError("file_type must not contain whitespace")
            object.__setattr__(self, "file_type", normalized)
        if self.mode is FilterMode.FILE_TYPE and not self.file_type:
            raise ValueError("ResourceFilter with mode=FILE_TYPE requires file_type")
        if self.mode is not FilterMode.FILE_TYPE and self.file_type is not None:
            raise ValueError(
                "file_type is only valid when mode=FILE_TYPE"
            )


def _confidence_matches(view: ResourceView, mode: FilterMode) -> bool:
    if mode is FilterMode.ALL:
        return True
    if mode is FilterMode.HIGH:
        return view.confidence == ConfidenceLevel.HIGH
    if mode is FilterMode.MEDIUM:
        return view.confidence == ConfidenceLevel.MEDIUM
    if mode is FilterMode.LOW:
        return view.confidence == ConfidenceLevel.LOW
    return False


def _extension_from_name(name: Optional[str]) -> str:
    if not name:
        return ""
    dot = name.rfind(".")
    if dot == -1 or dot == len(name) - 1:
        return ""
    return name[dot + 1:].lower()


def _media_from_content_type(content_type: Optional[str]) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def _matches_file_type(view: ResourceView, file_type: str) -> bool:
    """Return True if the resource matches the requested file type.

    Matching is case-insensitive and checks:
      * the URL's extension
      * the filename's extension
      * a basic content-type / extension overlap (e.g. `application/zip`
        matches `zip`)

    Resources that are missing a filename and a content-type are safe:
    they simply do not match.
    """
    if not file_type:
        return False
    target = file_type.lower()

    file_obj = view.file
    name = file_obj.name or ""
    url = file_obj.url or ""

    if _extension_from_name(name) == target:
        return True
    if _extension_from_name(url) == target:
        return True

    media = _media_from_content_type(file_obj.content_type)
    if media:
        subtype = media.split("/", 1)[-1]
        if subtype == target:
            return True
        if media == f"application/{target}":
            return True
    return False


def apply_filter(
    resources: Iterable[ResourceView],
    filter_spec: ResourceFilter,
) -> list[ResourceView]:
    """Return resources that pass `filter_spec`, preserving input order.

    The input iterable is not mutated and the returned list is a new list
    of references to the original `ResourceView` instances.
    """
    if not isinstance(filter_spec, ResourceFilter):
        raise TypeError("filter_spec must be a ResourceFilter instance")

    if filter_spec.mode is FilterMode.FILE_TYPE:
        target = filter_spec.file_type or ""
        return [r for r in resources if _matches_file_type(r, target)]

    return [r for r in resources if _confidence_matches(r, filter_spec.mode)]
