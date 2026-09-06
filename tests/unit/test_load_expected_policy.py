"""The gate's warm assertion has to follow the bundle, not name one family.

These pin two things that are easy to get wrong in opposite directions: the
derivation must actually change when the champion does (or the fix is pointless),
and it must never *fail* — a gate that cannot read a manifest has to behave as it
did before this module existed, because otherwise landing it ahead of the bundle
it was written for would break every existing run.
"""

from __future__ import annotations

import json

import pytest

from synthetic.load.expected_policy import DEFAULT_POLICY, expected_policy, retriever_family

_SCHEMA_1 = {
    "schema_version": 1,
    "candidate": {"artifact_type": "item-item-cosine", "version": "demo-itemitem-v1"},
    "ranker": {"artifact_type": "lightgbm-lambdarank"},
}
_SCHEMA_2_SASREC = {
    "schema_version": 2,
    "retriever": {"family": "sasrec", "artifacts": {}},
    "rankers": {"learned": {}, "fallback": {}},
}


def test_a_schema_1_bundle_reads_as_the_incumbent() -> None:
    # The behaviour every existing run depends on, unchanged.
    assert expected_policy(json.dumps(_SCHEMA_1)) == DEFAULT_POLICY


def test_a_sasrec_bundle_reads_as_its_own_family() -> None:
    # The point of the change: the same assertion, following the champion.
    assert expected_policy(json.dumps(_SCHEMA_2_SASREC)) == "sasrec+lightgbm"


def test_schema_1_derives_the_family_from_the_candidate_artifact_type() -> None:
    # Not a guess: schema 1 predates the `retriever` field, and its candidate
    # artifact type is literally the family name schema 2 records.
    assert retriever_family(_SCHEMA_1) == "item-item-cosine"


@pytest.mark.parametrize(
    "raw",
    ["", "not json at all", "[]", "null", '{"retriever": {"family": ""}}', '{"candidate": {}}'],
)
def test_anything_unreadable_falls_back_rather_than_failing(raw: str) -> None:
    # A gate that cannot read a manifest must run exactly as it did before this
    # module existed. Refusing would make the change unsafe to land early.
    assert expected_policy(raw) == DEFAULT_POLICY


def test_a_future_family_needs_no_change_here() -> None:
    # The derivation is not a whitelist — a family this code has never heard of
    # composes correctly, which is the whole reason not to enumerate them.
    document = json.dumps({"schema_version": 2, "retriever": {"family": "two-tower"}})

    assert expected_policy(document) == "two-tower+lightgbm"
