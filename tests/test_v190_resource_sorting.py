"""V1.9.0 Phase 3 — Resource sorting tests.

Validates the sorting contract:
  * Pure Python, no I/O.
  * Does not mutate the input list.
  * Returns a new list of references to the original `ResourceView` instances.
  * Typed `SortMode` enum; no stringly-typed modes.
  * All orderings are total orderings; ties are broken deterministically.
  * Stable: the input order is the final tie-breaker for every sort mode.
  * Safe for missing filename / size / score.
"""
from __future__ import annotations

import pytest

from app.core.models import DownloadFile
from app.services.analysis_view import ResourceView
from app.services.sorting import (
    SortMode,
    SortSpec,
    recommended_key,
    sort_resources,
)


def _view(
    name: str,
    *,
    score: int = 50,
    confidence: str = "medium",
    size: int | None = None,
    url: str | None = None,
) -> ResourceView:
    return ResourceView(
        file=DownloadFile(
            name=name,
            url=url or f"https://example.com/{name}",
            size=size,
        ),
        confidence=confidence,
        score=score,
    )


def test_recommended_orders_high_confidence_first():
    views = [
        _view("a.zip", confidence="low", score=99),
        _view("b.zip", confidence="high", score=10),
        _view("c.zip", confidence="medium", score=80),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["b.zip", "c.zip", "a.zip"]


def test_recommended_uses_score_within_same_confidence():
    views = [
        _view("first.zip", confidence="high", score=20),
        _view("second.zip", confidence="high", score=90),
        _view("third.zip", confidence="high", score=50),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["second.zip", "third.zip", "first.zip"]


def test_recommended_known_size_beats_unknown():
    views = [
        _view("known.zip", confidence="high", score=80, size=10),
        _view("unknown.zip", confidence="high", score=80),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["known.zip", "unknown.zip"]


def test_recommended_larger_size_first_when_other_keys_equal():
    views = [
        _view("small.zip", confidence="high", score=80, size=10),
        _view("big.zip", confidence="high", score=80, size=1_000_000),
        _view("medium.zip", confidence="high", score=80, size=500),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["big.zip", "medium.zip", "small.zip"]


def test_recommended_filename_alphabetical_within_equal_precedence():
    views = [
        _view("zeta.zip", confidence="high", score=80, size=1000),
        _view("alpha.zip", confidence="high", score=80, size=1000),
        _view("beta.zip", confidence="high", score=80, size=1000),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["alpha.zip", "beta.zip", "zeta.zip"]


def test_recommended_filename_is_case_insensitive():
    views = [
        _view("Bravo.zip", confidence="high", score=80, size=1000),
        _view("alpha.zip", confidence="high", score=80, size=1000),
        _view("charlie.zip", confidence="high", score=80, size=1000),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["alpha.zip", "Bravo.zip", "charlie.zip"]


def test_recommended_original_index_is_final_tie_breaker():
    views = [
        _view("a.zip", confidence="high", score=80, size=1000),
        _view("b.zip", confidence="high", score=80, size=1000),
        _view("c.zip", confidence="high", score=80, size=1000),
    ]
    out = sort_resources(views, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in out] == ["a.zip", "b.zip", "c.zip"]


def test_recommended_with_unknown_confidence_does_not_crash():
    v = _view("weird.bin", confidence="uncertain", score=80)
    out = sort_resources([v], SortSpec(SortMode.RECOMMENDED))
    assert out == [v]


def test_score_ascending_orders_lowest_first():
    views = [
        _view("a.zip", score=90),
        _view("b.zip", score=10),
        _view("c.zip", score=50),
    ]
    out = sort_resources(views, SortSpec(SortMode.SCORE_ASC))
    assert [v.score for v in out] == [10, 50, 90]


def test_score_descending_orders_highest_first():
    views = [
        _view("a.zip", score=90),
        _view("b.zip", score=10),
        _view("c.zip", score=50),
    ]
    out = sort_resources(views, SortSpec(SortMode.SCORE_DESC))
    assert [v.score for v in out] == [90, 50, 10]


def test_size_ascending_orders_smallest_first():
    views = [
        _view("a.zip", size=200),
        _view("b.zip", size=50),
        _view("c.zip", size=1000),
    ]
    out = sort_resources(views, SortSpec(SortMode.SIZE_ASC))
    assert [v.file.size for v in out] == [50, 200, 1000]


def test_size_descending_orders_largest_first():
    views = [
        _view("a.zip", size=200),
        _view("b.zip", size=50),
        _view("c.zip", size=1000),
    ]
    out = sort_resources(views, SortSpec(SortMode.SIZE_DESC))
    assert [v.file.size for v in out] == [1000, 200, 50]


def test_size_ascending_treats_unknown_size_as_largest():
    views = [
        _view("a.zip", size=200),
        _view("b.zip", size=None),
        _view("c.zip", size=50),
    ]
    out = sort_resources(views, SortSpec(SortMode.SIZE_ASC))
    assert [v.file.name for v in out] == ["c.zip", "a.zip", "b.zip"]


def test_size_descending_treats_unknown_size_as_smallest():
    views = [
        _view("a.zip", size=200),
        _view("b.zip", size=None),
        _view("c.zip", size=50),
    ]
    out = sort_resources(views, SortSpec(SortMode.SIZE_DESC))
    assert [v.file.name for v in out] == ["a.zip", "c.zip", "b.zip"]


def test_filename_ascending_is_alphabetical_case_insensitive():
    views = [
        _view("Charlie.zip"),
        _view("alpha.zip"),
        _view("Bravo.zip"),
    ]
    out = sort_resources(views, SortSpec(SortMode.FILENAME_ASC))
    assert [v.file.name for v in out] == ["alpha.zip", "Bravo.zip", "Charlie.zip"]


def test_filename_descending_is_reverse_alphabetical():
    views = [
        _view("alpha.zip"),
        _view("Charlie.zip"),
        _view("Bravo.zip"),
    ]
    out = sort_resources(views, SortSpec(SortMode.FILENAME_DESC))
    assert [v.file.name for v in out] == ["Charlie.zip", "Bravo.zip", "alpha.zip"]


def test_filename_sort_uses_original_index_for_missing_names():
    views = [
        _view(""),
        _view(""),
        _view("real.zip"),
    ]
    out = sort_resources(views, SortSpec(SortMode.FILENAME_ASC))
    assert [v.file.name for v in out] == ["", "", "real.zip"]


def test_missing_size_does_not_raise():
    v = _view("a.zip", size=None)
    out = sort_resources([v], SortSpec(SortMode.SIZE_ASC))
    assert out == [v]


def test_equal_scores_break_tie_by_filename_then_index():
    views = [
        _view("b.zip", score=50),
        _view("a.zip", score=50),
        _view("c.zip", score=50),
    ]
    out = sort_resources(views, SortSpec(SortMode.SCORE_ASC))
    assert [v.file.name for v in out] == ["a.zip", "b.zip", "c.zip"]


def test_equal_sizes_break_tie_by_filename_then_index():
    views = [
        _view("b.zip", size=100),
        _view("a.zip", size=100),
        _view("c.zip", size=100),
    ]
    out = sort_resources(views, SortSpec(SortMode.SIZE_ASC))
    assert [v.file.name for v in out] == ["a.zip", "b.zip", "c.zip"]


def test_does_not_mutate_input_list():
    views = [
        _view("c.zip", score=10),
        _view("a.zip", score=90),
        _view("b.zip", score=50),
    ]
    snapshot = [v.file.name for v in views]
    _ = sort_resources(views, SortSpec(SortMode.SCORE_DESC))
    assert [v.file.name for v in views] == snapshot


def test_returns_same_resource_instances():
    views = [
        _view("c.zip", score=10),
        _view("a.zip", score=90),
    ]
    out = sort_resources(views, SortSpec(SortMode.SCORE_DESC))
    assert out[0] is views[1]
    assert out[1] is views[0]


def test_empty_input_returns_empty_list():
    out = sort_resources([], SortSpec(SortMode.RECOMMENDED))
    assert out == []


def test_single_resource_returns_single():
    v = _view("only.zip", score=10)
    out = sort_resources([v], SortSpec(SortMode.RECOMMENDED))
    assert out == [v]


def test_duplicate_resource_objects():
    v = _view("dup.zip", score=50)
    out = sort_resources([v, v, v], SortSpec(SortMode.SCORE_ASC))
    assert out == [v, v, v]


def test_invalid_sort_mode_raises():
    with pytest.raises(TypeError):
        SortSpec("recommended")  # type: ignore[arg-type]


def test_invalid_sort_spec_raises():
    with pytest.raises(TypeError):
        sort_resources([], "recommended")  # type: ignore[arg-type]


def test_stable_for_identical_items():
    v1 = _view("a.zip", confidence="high", score=80, size=1000)
    v2 = _view("a.zip", confidence="high", score=80, size=1000)
    v3 = _view("a.zip", confidence="high", score=80, size=1000)
    out = sort_resources([v1, v2, v3], SortSpec(SortMode.SCORE_ASC))
    assert out == [v1, v2, v3]


def test_composition_after_filtering():
    """Sorting composed with filtering preserves the pipeline order."""
    from app.services.filtering import FilterMode, ResourceFilter, apply_filter

    views = [
        _view("setup.exe", confidence="low", score=99),
        _view("game.zip", confidence="high", score=10),
        _view("readme.txt", confidence="medium", score=80),
        _view("manual.pdf", confidence="high", score=50),
    ]
    filtered = apply_filter(views, ResourceFilter(FilterMode.HIGH))
    sorted_view = sort_resources(filtered, SortSpec(SortMode.RECOMMENDED))
    assert [v.file.name for v in sorted_view] == ["manual.pdf", "game.zip"]


def test_recommended_key_is_deterministic_for_same_input():
    v = _view("a.zip", confidence="high", score=80, size=1000)
    a = recommended_key(v, 0)
    b = recommended_key(v, 0)
    assert a == b
