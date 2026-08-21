"""Train deterministic serving artifacts for the compact portfolio tenant."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, create_engine, text

from src.config import Settings
from src.features import FEATURE_COLUMNS, FeatureIndex
from src.features.materialize import materialize
from src.models.artifacts import ArtifactRef, CandidateIndex, ServingManifest, file_sha256
from src.models.ranker.lgbm import LGBMRanker, LGBMRankerConfig

logger = logging.getLogger(__name__)

CANDIDATE_VERSION = "demo-itemitem-v1"
RANKER_VERSION = "demo-lgbm-v1"
FEATURE_VERSION = "feast-phase3-v1"

_RATINGS_SQL = text(
    'SELECT "userId" AS user_id, "movieId" AS item_id, rating, timestamp '
    "FROM ratings WHERE tenant_id = :tenant_id AND timestamp < :as_of_epoch "
    'ORDER BY timestamp, "userId", "movieId"'
)

_MOVIES_SQL = text('SELECT "movieId", genres FROM movies ORDER BY "movieId"')


def load_tenant_ratings(
    engine: Engine,
    *,
    tenant_id: str,
    as_of: datetime,
) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql(
            _RATINGS_SQL,
            connection,
            params={"tenant_id": tenant_id, "as_of_epoch": int(as_of.timestamp())},
        )


def build_ranker_training_data(
    ratings: pd.DataFrame,
    *,
    movies: pd.DataFrame,
    candidate_index: CandidateIndex,
    negatives_per_positive: int = 20,
) -> tuple[pd.DataFrame, list[int], np.ndarray]:
    """Build strict point-in-time groups that mirror serving candidates."""
    ordered = ratings.sort_values(["timestamp", "user_id", "item_id"], kind="stable")
    if ordered["item_id"].nunique() < 2:
        raise ValueError("serving artifact training requires at least two tenant items")
    feature_ratings = ordered.rename(columns={"user_id": "userId", "item_id": "movieId"})
    feature_index = FeatureIndex.build(feature_ratings, movies)

    feature_blocks: list[pd.DataFrame] = []
    labels: list[float] = []
    group_sizes: list[int] = []
    live_histories: dict[int, list[int]] = {}
    for event in ordered.itertuples(index=False):
        user_id = int(event.user_id)
        positive_id = int(event.item_id)
        as_of = int(event.timestamp)
        live_history = live_histories.setdefault(user_id, [])
        if not live_history:
            live_history.append(positive_id)
            continue
        candidates = candidate_index.retrieve(
            live_history,
            limit=negatives_per_positive + 1,
        ).movie_ids
        if positive_id not in candidates:
            live_history.append(positive_id)
            continue
        negatives = [
            item_id
            for item_id in candidates
            if item_id != positive_id and item_id not in live_history
        ][:negatives_per_positive]
        if not negatives:
            live_history.append(positive_id)
            continue
        group_items = [positive_id, *negatives]
        queries = pd.DataFrame(
            {
                "userId": [user_id] * len(group_items),
                "movieId": group_items,
                "as_of_timestamp": [as_of] * len(group_items),
            }
        )
        feature_blocks.append(feature_index.features_for(queries))
        # ADR 0002: every observed rating is one positive interaction; the
        # explicit star value never changes the implicit-feedback label.
        labels.extend([1.0, *([0.0] * len(negatives))])
        group_sizes.append(len(group_items))
        live_history.append(positive_id)

    if not feature_blocks or not group_sizes:
        raise ValueError("serving artifact training produced no warm ranking groups")
    return (
        pd.concat(feature_blocks, ignore_index=True)[FEATURE_COLUMNS],
        group_sizes,
        np.asarray(labels),
    )


def train_serving_artifacts(
    settings: Settings,
    *,
    output_dir: Path,
    as_of: datetime,
) -> ServingManifest:
    """Fit both stages offline and publish a checksum-pinned manifest last."""
    engine = create_engine(settings.admin_user_database_url, future=True)
    try:
        ratings = load_tenant_ratings(
            engine,
            tenant_id=settings.model_tenant_id,
            as_of=as_of,
        )
        with engine.connect() as connection:
            movies = pd.read_sql(_MOVIES_SQL, connection)
    finally:
        engine.dispose()
    if ratings.empty:
        raise ValueError(f"tenant {settings.model_tenant_id!r} has no ratings to train on")

    histories = {
        int(user_id): {int(item_id) for item_id in group["item_id"]}
        for user_id, group in ratings.groupby("user_id", sort=True)
    }
    index = CandidateIndex.build(histories, max_neighbors=100)
    features, groups, labels = build_ranker_training_data(
        ratings,
        movies=movies,
        candidate_index=index,
    )
    ranker = LGBMRanker(
        config=LGBMRankerConfig(
            num_leaves=15,
            learning_rate=0.05,
            min_data_in_leaf=2,
            num_boost_round=40,
            feature_fraction=1.0,
            bagging_fraction=1.0,
            bagging_freq=0,
            seed=42,
        )
    ).fit(features, groups, labels)

    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = output_dir / "candidate-index.json"
    ranker_path = output_dir / "ranker.txt"
    candidate_tmp = output_dir / ".candidate-index.json.tmp"
    ranker_tmp = output_dir / ".ranker.txt.tmp"
    index.write(candidate_tmp)
    ranker.save_model(ranker_tmp)
    candidate_tmp.replace(candidate_path)
    ranker_tmp.replace(ranker_path)

    manifest = ServingManifest(
        tenant_id=settings.model_tenant_id,
        candidate=ArtifactRef(
            artifact_type="item-item-cosine",
            version=CANDIDATE_VERSION,
            filename=candidate_path.name,
            sha256=file_sha256(candidate_path),
        ),
        ranker=ArtifactRef(
            artifact_type="lightgbm-lambdarank",
            version=RANKER_VERSION,
            filename=ranker_path.name,
            sha256=file_sha256(ranker_path),
        ),
        feature_version=FEATURE_VERSION,
        trained_at=as_of.astimezone(UTC).isoformat(),
    )
    manifest_tmp = output_dir / f".{settings.model_manifest_name}.tmp"
    manifest.write(manifest_tmp)
    manifest_tmp.replace(output_dir / settings.model_manifest_name)
    return manifest


def materialize_and_train(settings: Settings, *, as_of: datetime | None = None) -> ServingManifest:
    timestamp = (as_of or datetime.now(UTC)).astimezone(UTC)
    counts = materialize(settings, as_of=timestamp)
    logger.info("Materialized Feast snapshots: %s", counts)
    manifest = train_serving_artifacts(
        settings,
        output_dir=settings.model_artifact_dir,
        as_of=timestamp,
    )
    logger.info(
        "Published serving artifacts tenant=%s candidate=%s ranker=%s features=%s",
        manifest.tenant_id,
        manifest.candidate.version,
        manifest.ranker.version,
        manifest.feature_version,
    )
    return manifest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    materialize_and_train(Settings())


if __name__ == "__main__":
    main()
