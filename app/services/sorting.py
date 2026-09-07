"""Pure resource sorting for V1.9.

Sorting answers the question *"in what order should the available resources
appear?"* It is intentionally separate from filtering, categorization, and
selection.

Hard rules:
  * Pure Python — no PySide6, no I/O, no resolver/probe calls.
  * Never mutates the input list.
  * Returns a new list of references to the original `ResourceView`
    instances (no copies, no replacements).
  * Strict, typed `SortMode` enum — no stringly-typed modes.
  * All orderings are total orderings; ties are broken deterministically.
  * Safe for missing filename / size / score.
  * Stable: the input order is the final tie-breaker for every sort mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.services.analysis_view import ResourceView


class SortMode(str, Enum):
    """Supported sort modes.

    RECOMMENDED is the default and is intentionally deterministic (not
    insertion-order or Python incidental). See `recommended_key` for the
    exact precedence.
    """

    RECOMMENDED = "recommended"
    SCORE_ASC = "score_asc"
    SCORE_DESC = "score_desc"
    SIZE_ASC = "size_asc"
    SIZE_DESC = "size_desc"
    FILENAME_ASC = "filename_asc"
    FILENAME_DESC = "filename_desc"


_CONFIDENCE_RANK = {
    "high": 3,
    "medium": 2,
    "low": 1,
    "rejected": 0,
}


@dataclass(frozen=True)
class SortSpec:
    """Sort specification.

    `mode` is the primary discriminator. Invalid modes are rejected at
    construction time so callers cannot silently default to an unintended
    ordering.
    """

    mode: SortMode = SortMode.RECOMMENDED

    def __post_init__(self):
        if not isinstance(self.mode, SortMode):
            raise TypeError(
                f"SortSpec.mode must be a SortMode, got {type(self.mode).__name__}"
            )


def _filename_key(name: Optional[str]) -> str:
    if not name:
        return ""
    slash = name.rfind("/")
    return (name[slash + 1:] if slash != -1 else name).lower()


def _has_size(view: ResourceView) -> bool:
    size = view.file.size
    return size is not None and size >= 0


def _has_filename(view: ResourceView) -> bool:
    return bool(view.file.name)


def recommended_key(view: ResourceView, original_index: int) -> tuple:
    """Deterministic key for `RECOMMENDED` ordering.

    Precedence (highest priority first):
      1. Confidence: high > medium > low > rejected
      2. Score (descending — higher score ranks first)
      3. Known size beats unknown size
      4. Size (descending — larger ranks first)
      5. Filename (ascending, case-insensitive — alphabetical)
      6. Original position (ascending — preserves source order)
    """
    confidence_rank = _CONFIDENCE_RANK.get(view.confidence, -1)
    has_size = _has_size(view)
    size = view.file.size if has_size else 0
    return (
        -confidence_rank,
        -view.score,
        0 if has_size else 1,
        -size,
        _filename_key(view.file.name),
        original_index,
    )


def _score_key(view: ResourceView, original_index: int) -> tuple:
    return (view.score, _filename_key(view.file.name), original_index)


def _score_desc_key(view: ResourceView, original_index: int) -> tuple:
    return (-view.score, _filename_key(view.file.name), original_index)


def _size_key(view: ResourceView, original_index: int) -> tuple:
    size = view.file.size
    if size is None:
        return (1, 0, _filename_key(view.file.name), original_index)
    return (0, size, _filename_key(view.file.name), original_index)


def _size_desc_key(view: ResourceView, original_index: int) -> tuple:
    size = view.file.size
    if size is None:
        return (1, 0, _filename_key(view.file.name), original_index)
    return (0, -size, _filename_key(view.file.name), original_index)


def _filename_asc_key(view: ResourceView, original_index: int) -> tuple:
    return (_filename_key(view.file.name), original_index)


def _filename_desc_key(view: ResourceView, original_index: int) -> tuple:
    return (_filename_key(view.file.name), original_index)


def _key_for(mode: SortMode, view: ResourceView, original_index: int) -> tuple:
    if mode is SortMode.RECOMMENDED:
        return recommended_key(view, original_index)
    if mode is SortMode.SCORE_ASC:
        return _score_key(view, original_index)
    if mode is SortMode.SCORE_DESC:
        return _score_desc_key(view, original_index)
    if mode is SortMode.SIZE_ASC:
        return _size_key(view, original_index)
    if mode is SortMode.SIZE_DESC:
        return _size_desc_key(view, original_index)
    if mode is SortMode.FILENAME_ASC:
        return _filename_asc_key(view, original_index)
    if mode is SortMode.FILENAME_DESC:
        return _filename_desc_key(view, original_index)
    raise ValueError(f"Unsupported sort mode: {mode!r}")


def sort_resources(
    resources: list[ResourceView],
    sort_spec: SortSpec,
) -> list[ResourceView]:
    """Return a new list of resources ordered by `sort_spec`.

    The input list is never mutated. The output list contains references
    to the same `ResourceView` instances (no copies).
    """
    if not isinstance(sort_spec, SortSpec):
        raise TypeError("sort_spec must be a SortSpec instance")

    mode = sort_spec.mode
    indexed = list(enumerate(resources))

    if mode is SortMode.FILENAME_DESC:
        indexed.sort(
            key=lambda pair: (_filename_key(pair[1].file.name), pair[0]),
            reverse=True,
        )
    else:
        indexed.sort(key=lambda pair: _key_for(mode, pair[1], pair[0]))

    return [view for _, view in indexed]
