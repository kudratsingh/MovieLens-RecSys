from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from sqlalchemy import Connection, bindparam, text

from src.evaluation.protocol import COLD_START_THRESHOLD
from src.features import FEATURE_COLUMNS
from src.serving.models import (
    ModelRankingResult,
    ModelServerChampionMismatchError,
    RankedModelItem,
)
from src.serving.orchestration import RecommendationCoordinator
from src.serving.policy import (
    ARTIFACT_SHA256_NOT_PINNED,
    CANDIDATE_SOURCE_POPULARITY_FALLBACK,
    EXCLUSION_FILTER_POLICY,
    RANKER_ROUTE_FALLBACK,
    RANKER_ROUTE_LEARNED,
    REASON_CHAMPION_MISMATCH,
    REASON_NO_CHAMPION,
    id_set_digest,
)
from src.serving.recommendations import RecommendationService, RecommendedMovie
from src.serving.tenancy import TenantChampion
from tests.unit.test_serving_recommendations import _connection

# What the demo tenant's registry row names (migration 0016 seeds the real one
# from the committed bundle). Every call below passes it, because ``recommend``
# takes the champion as a required argument — a caller that has not decided
# which model a tenant is on has not decided whether it may serve.
DEMO_CHAMPION = TenantChampion(
    candidate_version="candidate-v1",
    ranker_version="ranker-v1",
    feature_version="features-v1",
)

# The history size the tests that are *not* about routing give their user. Two
# clear of the boundary rather than one, because several of them dismiss a
# title mid-test: at exactly the threshold a dismissal would silently move the
# user onto the popularity fallback and the assertion under test would start
# passing or failing for a reason it was never about. Derived from the constant
# so that moving the threshold moves the fixture with it.
_WARM_HISTORY = COLD_START_THRESHOLD + 2

# Two catalog titles the warm user has not seen: one for the sidecar stub to
# return as a learned result, one more so a test can offer two and have the
# exclusion filter drop only the first.
_UNSEEN_MOVIE = _WARM_HISTORY + 1
_OTHER_UNSEEN_MOVIE = _WARM_HISTORY + 2
_CATALOG_SIZE = _OTHER_UNSEEN_MOVIE

# The ids `_add_catalog_and_history` marks watched at `_WARM_HISTORY`.
_WATCHED_IDS = list(range(1, _WARM_HISTORY + 1))


class _LearnedModels:
    """Sidecar stub that records exactly what the coordinator handed it.

    The reported ``seed_count`` mirrors the real sidecar rule — positive
    history minus dismissals — instead of echoing the offered history. A stub
    that always echoed the history is what let a retrieval no seed reached keep
    reporting itself as learned two-stage serving.
    """

    def __init__(self, movie_id: int | list[int] = 3, *, seed_count: int | None = None) -> None:
        self._movie_ids = [movie_id] if isinstance(movie_id, int) else list(movie_id)
        self._seed_count = seed_count
        self.calls: list[dict[str, object]] = []

    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        positives = list(kwargs["positive_history_movie_ids"])
        dismissed = set(kwargs.get("dismissed_movie_ids") or ())
        seeds_used = (
            self._seed_count
            if self._seed_count is not None
            else len([movie_id for movie_id in positives if movie_id not in dismissed])
        )
        return ModelRankingResult(
            tenant_id=str(kwargs["tenant_id"]),
            candidate_policy="item-item-cosine",
            candidate_version="candidate-v1",
            ranker_version="ranker-v1",
            feature_version="features-v1",
            candidate_latency_ms=0.2,
            feature_latency_ms=2.5,
            ranker_latency_ms=0.4,
            latency_ms=3.2,
            items=[
                RankedModelItem(
                    movie_id=movie_id,
                    score=0.75,
                    features={column: 1.0 for column in FEATURE_COLUMNS},
                    candidate_source="item-item-cosine",
                    seed_movie_id=1,
                )
                for movie_id in self._movie_ids
            ],
            candidate_sources={"item-item-cosine": len(self._movie_ids)},
            seed_count=seeds_used,
            excluded_count=len(list(kwargs["excluded_movie_ids"] or ())),
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=1_760_000_000.0,
        )


class _UnavailableModels:
    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("offline")


# A digest of the right shape, because the coordinator refuses anything else and
# an audit row that cannot be replayed is the failure these fields exist to stop.
_SASREC_ENCODER_SHA256 = "a" * 64


class _SequenceModels(_LearnedModels):
    """The same stub answering as a loaded SASRec bundle rather than item-item.

    The point of the subclass is the provenance, not the ranking: with a champion
    swap pending, the coordinator has to carry whatever the sidecar says about
    which family answered and from which weights, unchanged. Anything it
    substitutes of its own would make the audit describe a deployment that never
    served the request.
    """

    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        return replace(
            await super().rank(**kwargs),
            candidate_policy="sasrec",
            retriever_family="sasrec",
            retriever_sha256=_SASREC_ENCODER_SHA256,
            ranker_route=RANKER_ROUTE_LEARNED,
            encoder_ms=1.4,
        )


def _add_catalog_and_history(
    connection: Connection,
    *,
    user_id: int,
    history_count: int,
) -> None:
    # The shared SQLite fixture starts with movies 1-3. Add enough rated
    # catalog items to test both sides of ADR 0001's boundary while leaving the
    # last two unseen for learned-result hydration.
    for movie_id in range(4, _CATALOG_SIZE + 1):
        connection.execute(
            text("INSERT INTO movies VALUES (:movie_id, :title, 'Drama')"),
            {"movie_id": movie_id, "title": f"Movie {movie_id}"},
        )
        connection.execute(
            text('INSERT INTO links ("movieId", "tmdbId") VALUES (:movie_id, NULL)'),
            {"movie_id": movie_id},
        )
    for movie_id in range(1, _CATALOG_SIZE + 1):
        connection.execute(
            text(
                "INSERT INTO ratings VALUES "
                "('demo', :background_user, :movie_id, 4.0, :timestamp)"
            ),
            {
                "background_user": 8000 + movie_id,
                "movie_id": movie_id,
                "timestamp": 1000 + movie_id,
            },
        )
    for movie_id in range(1, history_count + 1):
        connection.execute(
            text("INSERT INTO ratings VALUES " "('demo', :user_id, :movie_id, 4.0, :timestamp)"),
            {
                "user_id": user_id,
                "movie_id": movie_id,
                "timestamp": 2000 + movie_id,
            },
        )
        connection.execute(
            text("""
                INSERT INTO user_movie_state (
                    tenant_id, user_id, movie_id, watched_at, rating,
                    rating_updated_at, state_version, updated_at
                ) VALUES ('demo', :user_id, :movie_id, :timestamp, 4.0,
                          :timestamp, 1, :timestamp)
                """),
            {
                "user_id": user_id,
                "movie_id": movie_id,
                "timestamp": 2000 + movie_id,
            },
        )


def _make_existing_user_warm(connection: Connection) -> None:
    # User 10 starts with two interactions; add enough more to clear ADR 0001's
    # threshold, without marking learned result movie 3 as seen.
    for offset, movie_id in enumerate(range(4, 4 + _WARM_HISTORY - 2)):
        timestamp = 300 + offset
        connection.execute(
            text("INSERT INTO movies VALUES (:movie_id, :title, 'Drama')"),
            {"movie_id": movie_id, "title": f"Warm {movie_id}"},
        )
        connection.execute(
            text('INSERT INTO links ("movieId", "tmdbId") VALUES (:movie_id, NULL)'),
            {"movie_id": movie_id},
        )
        connection.execute(
            text("INSERT INTO ratings VALUES ('demo', 10, :movie_id, 4.0, :timestamp)"),
            {"movie_id": movie_id, "timestamp": timestamp},
        )
        connection.execute(
            text("""
                INSERT INTO user_movie_state (
                    tenant_id, user_id, movie_id, watched_at, rating,
                    rating_updated_at, state_version, updated_at
                ) VALUES ('demo', 10, :movie_id, :timestamp, 4.0,
                          :timestamp, 1, :timestamp)
                """),
            {"movie_id": movie_id, "timestamp": timestamp},
        )


@pytest.mark.asyncio
async def test_warm_user_routes_through_learned_two_stage_policy() -> None:
    connection = _connection()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=5, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "item-item-cosine+lightgbm"
    assert decision.model_version == "candidate-v1/ranker-v1"
    assert decision.fallback_reason is None
    assert decision.feature_latency_ms == 2.5
    assert [item.movie_id for item in decision.items] == [3]
    assert decision.predictions[0].features == {column: 1.0 for column in FEATURE_COLUMNS}


@pytest.mark.asyncio
async def test_cold_user_routes_to_popularity_without_calling_models() -> None:
    connection = _connection()
    try:
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=999, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "cold-start"
    assert decision.feature_latency_ms == 0.0
    assert [item.movie_id for item in decision.items] == [1, 2]
    assert [prediction.features for prediction in decision.predictions] == [{}, {}]


@pytest.mark.asyncio
async def test_a_sequence_answer_carries_the_family_artifact_route_and_encoder_time() -> None:
    """The audit has to be able to tell a SASRec answer from an item-item one.

    Every field here is the sidecar's, verbatim. The checksum in particular:
    ``candidate_version`` names a bundle and a version string can be republished
    over different weights, so the digest is the only part of the row that pins
    which weights actually produced these candidates.
    """
    connection = _connection()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(
            RecommendationService(), _SequenceModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=5, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.retriever_family == "sasrec"
    assert decision.retriever_sha256 == _SASREC_ENCODER_SHA256
    assert decision.ranker_route == RANKER_ROUTE_LEARNED
    assert decision.encoder_ms == 1.4


@pytest.mark.asyncio
async def test_an_item_item_answer_reports_no_encoder_time() -> None:
    """0.0 is a measurement for a family with no encoder, not a missing value."""
    connection = _connection()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=5, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    # The stub omits the field entirely, the way a sidecar mid-rolling-deploy
    # would; the family it has always sent as ``candidate_policy`` stands in.
    assert decision.retriever_family == "item-item-cosine"
    assert decision.encoder_ms == 0.0


@pytest.mark.asyncio
async def test_a_cold_user_records_the_fallback_route_and_never_the_learned_values() -> None:
    """The fallback path has to state its own provenance rather than inherit any.

    ``fallback`` rather than a third route value: a reader asking which requests
    missed the learned booster has to find these, and popularity ran neither
    booster nor encoder nor any checksum-pinned artifact.
    """
    connection = _connection()
    try:
        decision = await RecommendationCoordinator(
            RecommendationService(), _SequenceModels()
        ).recommend(connection, tenant_id="demo", user_id=999, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.fallback_reason == "cold-start"
    assert decision.retriever_family == CANDIDATE_SOURCE_POPULARITY_FALLBACK
    assert decision.retriever_sha256 == ARTIFACT_SHA256_NOT_PINNED
    assert decision.ranker_route == RANKER_ROUTE_FALLBACK
    assert decision.encoder_ms == 0.0


@pytest.mark.parametrize("history_count", [0, 1, 3, COLD_START_THRESHOLD - 1])
@pytest.mark.asyncio
async def test_short_history_routes_to_cold_start_fallback(history_count: int) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "cold-start"


@pytest.mark.parametrize("history_count", [COLD_START_THRESHOLD, _WARM_HISTORY])
@pytest.mark.asyncio
async def test_history_at_or_above_threshold_routes_to_learned_policy(
    history_count: int,
) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "item-item-cosine+lightgbm"
    assert decision.fallback_reason is None


@pytest.mark.asyncio
async def test_duplicate_rating_rows_do_not_satisfy_warm_threshold() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=1)
        for timestamp in range(3000, 3004):
            connection.execute(
                text("INSERT INTO ratings VALUES " "('demo', 77, 1, 4.0, :timestamp)"),
                {"timestamp": timestamp},
            )
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "cold-start"


@pytest.mark.asyncio
async def test_model_failure_routes_warm_user_to_popularity() -> None:
    connection = _connection()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=10, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "model-server-unavailable"
    assert [item.movie_id for item in decision.items] == [3]


def _dismiss(connection: Connection, *, user_id: int, movie_id: int, timestamp: int) -> None:
    connection.execute(
        text("""
            INSERT INTO user_movie_state (
                tenant_id, user_id, movie_id, dismissed_at, state_version, updated_at
            ) VALUES ('demo', :user_id, :movie_id, :timestamp, 1, :timestamp)
            ON CONFLICT (tenant_id, user_id, movie_id) DO UPDATE SET
                dismissed_at = excluded.dismissed_at,
                state_version = user_movie_state.state_version + 1,
                updated_at = excluded.updated_at
            """),
        {"user_id": user_id, "movie_id": movie_id, "timestamp": timestamp},
    )


@pytest.mark.asyncio
async def test_positive_history_and_exclusions_reach_the_sidecar_separately() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=_UNSEEN_MOVIE)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        _dismiss(connection, user_id=77, movie_id=_OTHER_UNSEEN_MOVIE, timestamp=4000)
        await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    call = models.calls[0]
    positives = list(call["positive_history_movie_ids"])  # type: ignore[arg-type]
    excluded = list(call["excluded_movie_ids"])  # type: ignore[arg-type]
    assert positives == list(range(_WARM_HISTORY, 0, -1))
    assert _OTHER_UNSEEN_MOVIE not in positives
    assert _OTHER_UNSEEN_MOVIE in excluded
    # Already-seen filtering stays part of the exclusion set, so the two inputs
    # overlap on watched titles without collapsing into one list.
    assert set(positives).issubset(set(excluded))


@pytest.mark.asyncio
async def test_watched_titles_hide_without_being_dropped_as_seeds() -> None:
    """The exclusion set contains this user's watched titles by construction.

    Handing it to the sidecar as the seed-suppression list emptied item-item
    retrieval for every warm user while the response still claimed learned
    two-stage serving. Dismissals travel on their own field for exactly this
    reason.
    """
    connection = _connection()
    models = _LearnedModels(movie_id=_UNSEEN_MOVIE)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        decision = await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    call = models.calls[0]
    positives = list(call["positive_history_movie_ids"])  # type: ignore[arg-type]
    assert set(positives).issubset(set(list(call["excluded_movie_ids"])))  # type: ignore[arg-type]
    assert list(call["dismissed_movie_ids"]) == []  # type: ignore[arg-type]
    assert decision.serving_policy.learned is True
    assert f"over {len(positives)} positive seeds" in decision.serving_policy.reason


@pytest.mark.asyncio
async def test_dismissals_reach_the_sidecar_on_their_own_field() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=_UNSEEN_MOVIE)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        _dismiss(connection, user_id=77, movie_id=_OTHER_UNSEEN_MOVIE, timestamp=4900)
        await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    call = models.calls[0]
    assert list(call["dismissed_movie_ids"]) == [_OTHER_UNSEEN_MOVIE]  # type: ignore[arg-type]
    assert _OTHER_UNSEEN_MOVIE in list(call["excluded_movie_ids"])  # type: ignore[arg-type]
    assert _OTHER_UNSEEN_MOVIE not in list(
        call["positive_history_movie_ids"]  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_a_retrieval_no_seed_reached_is_not_reported_as_learned() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE, seed_count=0)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.serving_policy.learned is False
    assert decision.serving_policy.name == "popularity-fill+lightgbm"
    assert decision.policy == decision.serving_policy.name
    assert decision.serving_policy.reason.startswith("unseeded-retrieval")
    assert f"{_WARM_HISTORY} positive watched signals" in decision.serving_policy.reason
    assert decision.fallback_reason == "unseeded-retrieval"
    # The ranker still ran, so the score is still an ordering score and the
    # items are still served — only the claim about the first stage changes.
    assert decision.serving_policy.score_scale == "lightgbm-rank-score"
    assert [item.movie_id for item in decision.items] == [_UNSEEN_MOVIE]


@pytest.mark.asyncio
async def test_reported_seed_count_is_the_number_of_seeds_retrieval_used() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE, seed_count=2)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    # A warm history, two of which the index could retrieve from: the
    # reason reports the seeds used and the signal count separately rather
    # than letting one stand in for the other.
    assert "over 2 positive seeds" in decision.serving_policy.reason
    assert decision.serving_policy.positive_signal_count == _WARM_HISTORY
    assert decision.serving_policy.learned is True


@pytest.mark.asyncio
async def test_dismissed_movie_is_never_a_positive_or_a_seed() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=_UNSEEN_MOVIE)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        # Movie 2 was watched first and then dismissed: it must stop being a
        # positive signal and must not seed retrieval.
        _dismiss(connection, user_id=77, movie_id=2, timestamp=4100)
        decision = await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    call = models.calls[0]
    assert 2 not in list(call["positive_history_movie_ids"])  # type: ignore[arg-type]
    assert 2 in list(call["excluded_movie_ids"])  # type: ignore[arg-type]
    assert decision.positive_signal_count == _WARM_HISTORY - 1


@pytest.mark.asyncio
async def test_dismissal_does_not_write_a_training_negative() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        before = connection.execute(text("SELECT COUNT(*) FROM ratings")).scalar_one()
        _dismiss(connection, user_id=77, movie_id=_OTHER_UNSEEN_MOVIE, timestamp=4200)
        await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
        after = connection.execute(text("SELECT COUNT(*) FROM ratings")).scalar_one()
        negative_rows = connection.execute(
            text("SELECT COUNT(*) FROM ratings " 'WHERE "userId" = 77 AND "movieId" = :movie_id'),
            {"movie_id": _OTHER_UNSEEN_MOVIE},
        ).scalar_one()
    finally:
        connection.close()

    assert after == before
    assert negative_rows == 0


@pytest.mark.asyncio
async def test_dismissed_movie_is_excluded_from_the_popularity_fallback() -> None:
    connection = _connection()
    try:
        # User 999 has no history, so this is the cold-start fallback path.
        _dismiss(connection, user_id=999, movie_id=1, timestamp=4300)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=999, limit=5, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert 1 not in [item.movie_id for item in decision.items]
    assert decision.excluded_count == 1


@pytest.mark.asyncio
async def test_dismissed_movie_is_dropped_during_metadata_hydration() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        _dismiss(connection, user_id=77, movie_id=_UNSEEN_MOVIE, timestamp=4400)
        # The sidecar stub still offers movie 11; hydration must not return it.
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels([_UNSEEN_MOVIE, _OTHER_UNSEEN_MOVIE])
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert _UNSEEN_MOVIE not in [item.movie_id for item in decision.items]
    assert _UNSEEN_MOVIE not in [prediction.movie_id for prediction in decision.predictions]


@pytest.mark.asyncio
async def test_final_validation_drops_an_excluded_id_that_survived_hydration() -> None:
    connection = _connection()
    models = _LearnedModels([_UNSEEN_MOVIE, _OTHER_UNSEEN_MOVIE])
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        _dismiss(connection, user_id=77, movie_id=_UNSEEN_MOVIE, timestamp=4500)
        decision = await RecommendationCoordinator(_LeakyHydration(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    # Hydration handed back the dismissed title; the last check has to catch it
    # rather than let an explicit "not for me" reappear.
    assert [item.movie_id for item in decision.items] == [_OTHER_UNSEEN_MOVIE]
    assert _UNSEEN_MOVIE not in [prediction.movie_id for prediction in decision.predictions]
    assert decision.fallback_reason is None
    assert f"excluded-id-blocked: [{_UNSEEN_MOVIE}]" in decision.reason


@pytest.mark.asyncio
async def test_learned_output_holding_only_excluded_ids_fails_closed() -> None:
    connection = _connection()
    models = _LearnedModels(movie_id=_UNSEEN_MOVIE)
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        _dismiss(connection, user_id=77, movie_id=_UNSEEN_MOVIE, timestamp=4600)
        decision = await RecommendationCoordinator(_LeakyHydration(), models).recommend(
            connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    assert decision.policy == "popularity"
    assert decision.fallback_reason == "excluded-id-blocked"
    assert decision.reason.startswith("excluded-id-blocked")
    assert _UNSEEN_MOVIE not in [item.movie_id for item in decision.items]


class _LeakyHydration(RecommendationService):
    """Hydration that ignores the caller's exclusion set.

    Stands in for a stage that has drifted from the request's view of state, so
    the coordinator's final validation has something real to catch.
    """

    def hydrate_ranked_movies(  # type: ignore[override]
        self,
        connection: Connection,
        **kwargs: object,
    ) -> list[RecommendedMovie]:
        ranked_items = list(kwargs["ranked_items"])  # type: ignore[call-overload]
        query = text(
            'SELECT "movieId", title FROM movies WHERE "movieId" IN :movie_ids'
        ).bindparams(bindparam("movie_ids", expanding=True))
        titles = {
            int(row[0]): str(row[1])
            for row in connection.execute(
                query, {"movie_ids": [movie_id for movie_id, _ in ranked_items]}
            )
        }
        return [
            RecommendedMovie(
                movie_id=movie_id,
                title=titles[movie_id],
                genres=[],
                tmdb_id=None,
                interaction_count=0,
                score=score,
                reason=str(kwargs["reason"]),
            )
            for movie_id, score in ranked_items
            if movie_id in titles
        ]


@pytest.mark.parametrize("history_count", [0, 1, 3])
@pytest.mark.asyncio
async def test_policy_reports_fallback_below_the_threshold(history_count: int) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _UnavailableModels()
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.serving_policy.name == "popularity"
    assert decision.serving_policy.learned is False
    assert decision.serving_policy.positive_signal_count == history_count
    assert decision.serving_policy.threshold == COLD_START_THRESHOLD
    assert decision.serving_policy.reason.startswith("cold-start")
    assert decision.serving_policy.score_scale == "tenant-interaction-count"
    assert decision.serving_policy.filter_policy == EXCLUSION_FILTER_POLICY


@pytest.mark.parametrize("history_count", [COLD_START_THRESHOLD, _WARM_HISTORY])
@pytest.mark.asyncio
async def test_policy_reports_learned_serving_at_or_above_the_threshold(
    history_count: int,
) -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=history_count)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.serving_policy.name == "item-item-cosine+lightgbm"
    assert decision.serving_policy.learned is True
    assert decision.serving_policy.positive_signal_count == history_count
    assert decision.serving_policy.threshold == COLD_START_THRESHOLD
    assert decision.serving_policy.reason.startswith("learned-two-stage")
    # A LambdaRank score is an ordering, not a probability. The response has to
    # say so or a client will render it as a match percentage.
    assert decision.serving_policy.score_scale == "lightgbm-rank-score"
    assert decision.policy == decision.serving_policy.name


@pytest.mark.asyncio
async def test_audit_payload_carries_input_state_exclusions_and_freshness() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        _dismiss(connection, user_id=77, movie_id=_OTHER_UNSEEN_MOVIE, timestamp=4700)
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert decision.input_state_revision > 0
    # The dismissed title was never watched, so it belongs to the exclusion
    # set and to neither the positives nor their digest.
    assert decision.input_state_hash == id_set_digest(_WATCHED_IDS)
    assert decision.exclusion_hash == id_set_digest([*_WATCHED_IDS, _OTHER_UNSEEN_MOVIE])
    assert decision.positive_signal_count == _WARM_HISTORY
    assert decision.excluded_count == _WARM_HISTORY + 1
    assert decision.filter_policy == EXCLUSION_FILTER_POLICY
    assert decision.feature_event_time is not None
    assert decision.candidate_sources == {"item-item-cosine": 1}
    assert decision.predictions[0].candidate_source == "item-item-cosine"
    assert decision.predictions[0].seed_movie_id == 1


@pytest.mark.asyncio
async def test_input_digests_are_order_independent_and_revision_moves_with_state() -> None:
    connection = _connection()
    try:
        _add_catalog_and_history(connection, user_id=77, history_count=_WARM_HISTORY)
        first = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
        second = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
        _dismiss(connection, user_id=77, movie_id=_OTHER_UNSEEN_MOVIE, timestamp=4800)
        third = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels(movie_id=_UNSEEN_MOVIE)
        ).recommend(connection, tenant_id="demo", user_id=77, limit=2, champion=DEMO_CHAMPION)
    finally:
        connection.close()

    assert first.input_state_hash == second.input_state_hash
    assert first.exclusion_hash == second.exclusion_hash
    assert first.input_state_revision == second.input_state_revision
    assert third.exclusion_hash != first.exclusion_hash
    assert third.input_state_revision > first.input_state_revision


class _MismatchedChampionModels:
    """Sidecar stub standing in for one that loaded a bundle nobody registered."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def rank(self, **kwargs) -> ModelRankingResult:  # type: ignore[no-untyped-def]
        self.calls.append(dict(kwargs))
        raise ModelServerChampionMismatchError(
            "tenant 'demo' is registered on candidate-v2/ranker-v1/features-v1 but this "
            "sidecar loaded candidate-v1/ranker-v1/features-v1"
        )


@pytest.mark.asyncio
async def test_a_tenant_with_no_champion_never_reaches_the_sidecar() -> None:
    """A registry row with no champion is a decision, not an outage.

    The user is warm and the sidecar would have answered; the response is still
    popularity, and it says why in terms an operator can act on rather than
    reporting the sidecar as unavailable.
    """
    connection = _connection()
    models = _LearnedModels()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="default", user_id=10, limit=2, champion=None
        )
    finally:
        connection.close()

    assert models.calls == []
    assert decision.policy == "popularity"
    assert decision.serving_policy.learned is False
    assert decision.fallback_reason == REASON_NO_CHAMPION
    assert decision.serving_policy.reason.startswith(f"{REASON_NO_CHAMPION}:")


@pytest.mark.asyncio
async def test_no_champion_is_reported_before_the_cold_start_threshold() -> None:
    """Precedence matters to the viewer.

    A cold user in a tenant with no champion would be told to collect five
    signals to unlock something the tenant cannot serve at any history size.
    The tenant's state is the more specific answer, so it wins.
    """
    connection = _connection()
    try:
        decision = await RecommendationCoordinator(
            RecommendationService(), _LearnedModels()
        ).recommend(connection, tenant_id="default", user_id=999, limit=2, champion=None)
    finally:
        connection.close()

    assert decision.fallback_reason == REASON_NO_CHAMPION


@pytest.mark.asyncio
async def test_the_champion_travels_to_the_sidecar_on_every_learned_call() -> None:
    connection = _connection()
    models = _LearnedModels()
    try:
        _make_existing_user_warm(connection)
        await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=10, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    assert models.calls[0]["champion"] is DEMO_CHAMPION


@pytest.mark.asyncio
async def test_a_refused_champion_is_audited_as_a_mismatch_not_an_outage() -> None:
    """The distinction the reason vocabulary exists for.

    A sidecar that refuses because it loaded a different bundle is healthy; a
    sidecar that times out is not. Both degrade to popularity, and an operator
    reading ``fallback_reason`` has to be able to tell a half-finished
    promotion from an outage without opening a log.
    """
    connection = _connection()
    models = _MismatchedChampionModels()
    try:
        _make_existing_user_warm(connection)
        decision = await RecommendationCoordinator(RecommendationService(), models).recommend(
            connection, tenant_id="demo", user_id=10, limit=2, champion=DEMO_CHAMPION
        )
    finally:
        connection.close()

    assert len(models.calls) == 1
    assert decision.policy == "popularity"
    assert decision.fallback_reason == REASON_CHAMPION_MISMATCH
    assert "candidate-v2" in decision.serving_policy.reason
