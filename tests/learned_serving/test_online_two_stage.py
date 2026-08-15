from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.features.online import create_feature_store
from src.models.artifacts import ServingArtifactBundle
from src.serving.model_server import ModelRankingService


def test_real_feast_features_rank_warm_demo_persona() -> None:
    settings = Settings()
    bundle = ServingArtifactBundle.load(
        Path(settings.model_artifact_dir) / settings.model_manifest_name
    )
    service = ModelRankingService(bundle, create_feature_store(settings))
    history = [6, 10, 32, 47, 110, 2028, 2571, 2959]

    result = service.rank(
        tenant_id="demo",
        user_id=900000101,
        history_movie_ids=history,
        limit=5,
        candidate_limit=20,
    )

    assert result.candidate_policy == "item-item-cosine"
    assert result.candidate_version == "demo-itemitem-v1"
    assert result.ranker_version == "demo-lgbm-v1"
    assert result.feature_version == "feast-phase3-v1"
    assert len(result.items) == 5
    assert not {item.movie_id for item in result.items} & set(history)
