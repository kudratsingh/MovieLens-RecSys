"""
Unit tests for the content trainer's run-shaping and coverage helpers.

The trainer itself needs Postgres and MLflow, so what is testable here is the
part that decides what the run *claims*: the name it is filed under and the
cold-item coverage counts that are this rung's primary evidence. The counts get
the attention, because a coverage number that is quietly computed over the wrong
population would read exactly like a real one.
"""

from __future__ import annotations

from src.models.candidates import routing
from src.training import content


def test_the_default_policy_keeps_the_plain_run_name() -> None:
    # The name docs/results.md will cite has to stay findable, so only a run
    # under the opt-out policy is renamed — the contract routing.run_name_for
    # holds for every candidate trainer.
    assert routing.run_name_for(content.BASE_RUN_NAME, routing.DEFAULT_POLICY) == (
        "content-genre-year"
    )
    assert routing.run_name_for(content.BASE_RUN_NAME, routing.POLICY_INDEX) == (
        "content-genre-year-index-routing"
    )


def test_the_model_type_names_the_representation() -> None:
    # Increment 2 scores TMDB metadata the same way; pooling its runs with these
    # would compare two representations as if they were one model.
    assert content.MODEL_TYPE == "content_genre_year"


def test_slate_coverage_counts_cold_items_across_the_served_slates() -> None:
    recommendations = {
        1: [10, 900, 901],
        2: [11, 900],
        3: [12, 13],
    }
    coverage = content.slate_coverage(
        recommendations, cold_items=[900, 901, 902], user_ids=[1, 2, 3]
    )

    assert coverage.n_users == 3
    assert coverage.n_distinct_items_retrieved == 6
    # 902 was never retrieved, so it does not count as reached.
    assert coverage.n_distinct_cold_items_retrieved == 2
    assert coverage.mean_cold_items_per_slate == 1.0
    assert coverage.n_users_with_a_cold_candidate == 2


def test_slate_coverage_ignores_users_it_was_not_asked_about() -> None:
    # The trainer passes its content-served users. A fallback-served user's slate
    # comes from the popularity ranking, which can never contain a cold item, so
    # pooling them would dilute the measurement with a population the model was
    # not asked about.
    recommendations = {1: [900], 2: [10]}
    coverage = content.slate_coverage(recommendations, cold_items=[900], user_ids=[1])

    assert coverage.n_users == 1
    assert coverage.mean_cold_items_per_slate == 1.0
    assert coverage.n_distinct_items_retrieved == 1


def test_slate_coverage_of_no_users_is_zero_rather_than_undefined() -> None:
    coverage = content.slate_coverage({}, cold_items=[900], user_ids=[])
    assert coverage.n_users == 0
    assert coverage.mean_cold_items_per_slate == 0.0
    assert coverage.n_distinct_cold_items_retrieved == 0


def test_a_user_with_no_slate_contributes_nothing() -> None:
    # recommend_for_users covers every id it is handed, but an absent key must
    # count as an empty slate rather than raising mid-report.
    coverage = content.slate_coverage({}, cold_items=[900], user_ids=[1, 2])
    assert coverage.n_users == 2
    assert coverage.n_users_with_a_cold_candidate == 0
    assert coverage.mean_cold_items_per_slate == 0.0
