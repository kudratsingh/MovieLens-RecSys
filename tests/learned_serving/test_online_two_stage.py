from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.features.online import create_feature_store
from src.models.artifacts import ServingArtifactBundle
from src.serving.model_server import ModelRankingService
from src.serving.policy import EXCLUSION_FILTER_POLICY

_HISTORY = [6, 10, 32, 47, 110, 2028, 2571, 2959]


def _service() -> ModelRankingService:
    settings = Settings()
    bundle = ServingArtifactBundle.load(
        Path(settings.model_artifact_dir) / settings.model_manifest_name
    )
    return ModelRankingService(bundle, create_feature_store(settings))


def test_real_feast_features_rank_warm_demo_persona() -> None:
    result = _service().rank(
        tenant_id="demo",
        user_id=900000101,
        positive_history_movie_ids=_HISTORY,
        excluded_movie_ids=[],
        limit=5,
        candidate_limit=20,
    )

    assert result.candidate_policy == "item-item-cosine"
    assert result.candidate_version == "demo-itemitem-v1"
    assert result.ranker_version == "demo-lgbm-v1"
    assert result.feature_version == "feast-phase3-v1"
    assert len(result.items) == 5
    assert not {item.movie_id for item in result.items} & set(_HISTORY)
    assert result.filter_policy == EXCLUSION_FILTER_POLICY
    assert result.seed_count == len(_HISTORY)
    assert sum(result.candidate_sources.values()) > 0


def test_excluded_ids_never_reach_the_ranked_output_against_seeded_stores() -> None:
    service = _service()
    baseline = service.rank(
        tenant_id="demo",
        user_id=900000101,
        positive_history_movie_ids=_HISTORY,
        excluded_movie_ids=[],
        limit=5,
        candidate_limit=20,
    )
    excluded = [item.movie_id for item in baseline.items[:2]]

    result = service.rank(
        tenant_id="demo",
        user_id=900000101,
        positive_history_movie_ids=_HISTORY,
        excluded_movie_ids=excluded,
        limit=5,
        candidate_limit=20,
    )

    assert not {item.movie_id for item in result.items} & set(excluded)
    assert result.excluded_count == len(excluded)
    assert len(result.items) == 5


def test_a_dismissed_history_item_is_dropped_from_the_seed_set() -> None:
    service = _service()
    dismissed = _HISTORY[0]

    result = service.rank(
        tenant_id="demo",
        user_id=900000101,
        positive_history_movie_ids=_HISTORY,
        excluded_movie_ids=[dismissed],
        limit=5,
        candidate_limit=20,
    )

    assert result.seed_count == len(_HISTORY) - 1
    assert dismissed not in {item.movie_id for item in result.items}
    assert dismissed not in {item.seed_movie_id for item in result.items}


def test_ranked_items_carry_candidate_attribution_for_the_audit() -> None:
    result = _service().rank(
        tenant_id="demo",
        user_id=900000101,
        positive_history_movie_ids=_HISTORY,
        excluded_movie_ids=[],
        limit=5,
        candidate_limit=20,
    )

    for item in result.items:
        assert item.candidate_source in {"item-item-cosine", "popularity-fill"}
        if item.candidate_source == "item-item-cosine":
            assert item.seed_movie_id in set(_HISTORY)
        else:
            assert item.seed_movie_id is None
