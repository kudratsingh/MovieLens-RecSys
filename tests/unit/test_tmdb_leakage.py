"""The one rule the TMDB snapshot must never break.

`vote_average`, `vote_count`, `popularity`, `budget`, `revenue` and `status` are
as-of-pull values. TMDB reports them as they stand on the day of the request,
with no per-observation history, so nothing can reconstruct what they were
during the MovieLens interaction window — which ends in 2019, years before the
snapshot. A model that used one of them as a feature against ADR 0001's temporal
split would be reading the future, and would say so by scoring better offline
than it can possibly score online. That is exactly the failure CLAUDE.md's
leakage warning names, and it is silent, which is why it gets a test rather than
a comment.

**The distinction this test has to get right** is the reason it is not a plain
substring grep. `item_popularity_all_time`, `item_popularity_30d` and
`item_popularity_7d` are already in the contract and are perfectly safe: they
are interaction counts computed from the training frame at the prediction
timestamp, so they *have* a point-in-time history and the feature pipeline uses
it. TMDB's `popularity` is a single number describing today. Same word, opposite
property — so the rule is about where the value came from, and a column is only
offending when it names a TMDB source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import src.feature_contract as feature_contract
from src.data.tmdb_schema import NOT_POINT_IN_TIME_SAFE_COLUMNS

CONTRACT_PATH = Path(feature_contract.__file__)


def _is_offending(column: str) -> str | None:
    """Return which unsafe TMDB value ``column`` names, or None if it is clean."""
    lowered = column.lower()
    for unsafe in NOT_POINT_IN_TIME_SAFE_COLUMNS:
        if lowered == unsafe:
            return unsafe
        # A TMDB-sourced variant: `tmdb_popularity`, `item_tmdb_vote_average`,
        # `revenue_tmdb`. The `tmdb` marker is what separates these from the
        # MovieLens-derived popularity windows already in the contract.
        if "tmdb" in lowered and re.search(rf"(?:^|_){re.escape(unsafe)}(?:_|$)", lowered):
            return unsafe
    return None


def _exported_column_lists() -> dict[str, list[str]]:
    """Every list-of-strings the contract module exports, whatever it is called.

    Enumerated rather than hardcoded to `FEATURE_COLUMNS` so a contract list
    added later — a sequence-aware ranker's, say — is covered by this test the
    day it appears, without anyone remembering to come back here.
    """
    return {
        name: list(value)
        for name, value in vars(feature_contract).items()
        if not name.startswith("_")
        and isinstance(value, (list, tuple))
        and value
        and all(isinstance(entry, str) for entry in value)
    }


def test_the_snapshot_names_its_unsafe_columns() -> None:
    """The list this test reads is the same one the migration comments from."""
    assert set(NOT_POINT_IN_TIME_SAFE_COLUMNS) == {
        "vote_average",
        "vote_count",
        "popularity",
        "budget",
        "revenue",
        "status",
    }


def test_no_feature_contract_list_references_an_as_of_pull_column() -> None:
    lists = _exported_column_lists()
    assert lists, "expected at least one exported column list in src/feature_contract.py"

    for list_name, columns in lists.items():
        for column in columns:
            unsafe = _is_offending(column)
            assert unsafe is None, (
                f"{list_name} contains '{column}', which names the as-of-pull TMDB value "
                f"'{unsafe}'. Those have no observation timestamp and cannot be "
                "reconstructed at a 2019 prediction time, so using one as a feature leaks "
                "the future into training. See migration 0018_tmdb_catalog and "
                "docs/data/tmdb-metadata.md."
            )


def test_the_existing_movielens_popularity_windows_are_not_flagged() -> None:
    """Guards the guard: a rule that banned these would be wrong, not strict.

    They are counted from the training frame at the prediction timestamp, which
    is the property TMDB's `popularity` lacks.
    """
    for column in ("item_popularity_all_time", "item_popularity_30d", "item_popularity_7d"):
        assert column in feature_contract.FEATURE_COLUMNS
        assert _is_offending(column) is None


@pytest.mark.parametrize(
    "column",
    [
        "popularity",
        "vote_average",
        "tmdb_popularity",
        "item_tmdb_vote_count",
        "tmdb_revenue",
        "movie_tmdb_budget",
        "tmdb_status",
    ],
)
def test_the_rule_catches_the_shapes_a_tmdb_feature_would_take(column: str) -> None:
    assert _is_offending(column) is not None


def test_the_contract_source_never_reaches_for_a_tmdb_value() -> None:
    """Catches a column defined somewhere this test cannot introspect."""
    source = CONTRACT_PATH.read_text(encoding="utf-8").lower()
    if "tmdb" not in source:
        return
    for line in source.splitlines():
        if "tmdb" not in line:
            continue
        for unsafe in NOT_POINT_IN_TIME_SAFE_COLUMNS:
            assert unsafe not in line, (
                f"src/feature_contract.py has a line naming both TMDB and '{unsafe}': "
                f"{line.strip()!r}. If this is deliberate and the value genuinely has a "
                "point-in-time history behind it, say so here and update this test."
            )
