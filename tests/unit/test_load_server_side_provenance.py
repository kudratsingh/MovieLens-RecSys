"""The load-gate evidence has to answer the question the swap is judged on.

`server-side.json` is the durable audit export a reviewer reads when the run is
long over and the database has moved on. Before migration 0019's four columns
were added to it, that file could say how fast a request was and which policy
served it, but not *which retriever answered, from which artifact, scored by
which route* — so confirming a champion swap after the fact meant a live psql
session against a database that may no longer hold the rows.

The empty-string case below is the one worth pinning. Two different facts share
one column:

    ''    the route ran no published artifact — the popularity fallback
          retrieves from a SQL query, so there is nothing to pin
    None  the row predates 0019 and nobody measured this

Collapsing them would make "requests served without a pinned artifact"
unanswerable, so the export keeps them distinct.
"""

from __future__ import annotations

from datetime import UTC, datetime

from synthetic.load.server_side import AUDIT_SQL, _shape_audit

_LEARNED_ROW = {
    "created_at": datetime(2026, 9, 6, 3, 48, tzinfo=UTC),
    "policy": "item-item-cosine+lightgbm",
    "outcome": "success",
    "http_status": 200,
    "user_id": 900000101,
    "retriever_family": "item-item-cosine",
    "retriever_sha256": "7fa2f3be3c1544fd2888bf343eb70de6b78ad9c626fab47d330f8b0d1ee0bcf0",
    "ranker_route": "learned",
    "encoder_ms": 0.0,
}


def test_the_export_carries_retrieval_provenance() -> None:
    shaped = _shape_audit(_LEARNED_ROW)

    assert shaped["retriever_family"] == "item-item-cosine"
    assert shaped["retriever_sha256"] == _LEARNED_ROW["retriever_sha256"]
    assert shaped["ranker_route"] == "learned"
    assert shaped["encoder_ms"] == 0.0


def test_an_unpinned_route_is_distinguishable_from_an_unmeasured_row() -> None:
    fallback = _shape_audit({**_LEARNED_ROW, "retriever_sha256": "", "ranker_route": "fallback"})
    predates_0019 = _shape_audit({**_LEARNED_ROW, "retriever_sha256": None})

    # Empty string: ran, but from no published artifact.
    assert fallback["retriever_sha256"] == ""
    assert fallback["retriever_sha256"] is not None
    # None: the row is older than the column.
    assert predates_0019["retriever_sha256"] is None


def test_a_zero_encoder_is_a_measurement_not_a_gap() -> None:
    # An item-item bundle runs no encoder and spends no time in one. 0.0 is the
    # answer; None would say nobody looked.
    assert _shape_audit(_LEARNED_ROW)["encoder_ms"] == 0.0
    assert _shape_audit({**_LEARNED_ROW, "encoder_ms": None})["encoder_ms"] is None


def test_the_query_selects_every_column_the_shaping_reports() -> None:
    # The two drift apart silently: a column added to one and not the other
    # exports as null forever and looks like missing data rather than a bug.
    for column in ("retriever_family", "retriever_sha256", "ranker_route", "encoder_ms"):
        assert column in AUDIT_SQL, f"{column} is shaped but not selected"
