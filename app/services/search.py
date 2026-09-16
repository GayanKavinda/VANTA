"""Pure resource text-search for V2.0 Phase 3.7.

Search answers the question *"which resources match this text query?"* It is a
local, non-mutating view over already-analyzed resources — it never performs a
network request and never re-runs the analyzer.

Hard rules (mirrors `app.services.filtering`):
  * Pure Python — no PySide6, no I/O, no resolver/probe calls.
  * Non-mutating — the input list and resource objects are never modified.
  * Order-preserving — output follows the input list order.
  * Returns references to the original `ResourceView` instances.
  * Case-insensitive substring matching.

Search is intentionally independent of selection state; it only narrows the
visible working set that filtering/sorting/grouping operate on. Filtering is
applied *downstream* of search (search first, then filter), so the two never
delete resources from the underlying analysis result.
"""
from __future__ import annotations

from typing import Iterable

from app.services.analysis_view import ResourceView


def search_resources(
    query: str,
    resources: Iterable[ResourceView],
) -> list[ResourceView]:
    """Return resources that match `query`, preserving input order.

    An empty or whitespace-only query returns every resource (as a new list of
    references to the original `ResourceView` instances). Matching is
    case-insensitive substring against useful existing fields:

      * filename (`file.name`)
      * final URL
      * source URL
      * content type (`mime_media_type` / `file.content_type`)
      * MIME category (`mime_category`)
      * HTML element / discovery type (`element_type`)
    """
    needle = (query or "").strip()
    if not needle:
        return list(resources)

    lowered = needle.lower()
    matches: list[ResourceView] = []

    for view in resources:
        if _matches(view, lowered):
            matches.append(view)

    return matches


def _matches(view: ResourceView, needle: str) -> bool:
    haystack = [
        view.file.name,
        view.file.url,
        view.final_url,
        view.source_url,
        view.file.content_type,
        getattr(view, "mime_media_type", None),
        view.mime_category,
        view.element_type,
        view.type_label,
    ]
    for value in haystack:
        if value and needle in value.lower():
            return True
    return False
