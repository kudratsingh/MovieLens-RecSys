"""Train deterministic serving artifacts for the compact portfolio tenant.

Run with no arguments this materializes Feast snapshots and republishes the
bundle into ``MODEL_ARTIFACT_DIR`` — the shape the demo stack's one-shot
``feature-setup`` job uses. The release build instead pins ``--as-of`` and
``--output-dir`` and passes ``--train-only``, which is what lets the committed
bundle be rebuilt and hash-compared (non-negotiable #5): a pinned ``as_of``
makes ``manifest.json`` byte-stable, and skipping materialization keeps a
rebuild from appending another generation of feature rows as a side effect.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

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


def publish_serving_artifacts(
    ratings: pd.DataFrame,
    *,
    movies: pd.DataFrame,
    tenant_id: str,
    output_dir: Path,
    as_of: datetime,
    manifest_name: str = "manifest.json",
) -> ServingManifest:
    """Fit both stages and publish a checksum-pinned manifest last.

    ``as_of`` is the only clock this function reads: it becomes the manifest's
    ``trained_at``, so a caller that pins it gets a byte-stable manifest for a
    given set of ratings.
    """
    if ratings.empty:
        raise ValueError(f"tenant {tenant_id!r} has no ratings to train on")

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
        tenant_id=tenant_id,
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
    manifest_tmp = output_dir / f".{manifest_name}.tmp"
    manifest.write(manifest_tmp)
    manifest_tmp.replace(output_dir / manifest_name)
    return manifest


def train_serving_artifacts(
    settings: Settings,
    *,
    output_dir: Path,
    as_of: datetime,
) -> ServingManifest:
    """Read the tenant's ratings as of ``as_of`` and publish the bundle."""
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
    return publish_serving_artifacts(
        ratings,
        movies=movies,
        tenant_id=settings.model_tenant_id,
        output_dir=output_dir,
        as_of=as_of,
        manifest_name=settings.model_manifest_name,
    )


def materialize_and_train(
    settings: Settings,
    *,
    as_of: datetime | None = None,
    output_dir: Path | None = None,
) -> ServingManifest:
    timestamp = (as_of or datetime.now(UTC)).astimezone(UTC)
    counts = materialize(settings, as_of=timestamp)
    logger.info("Materialized Feast snapshots: %s", counts)
    return train_serving_artifacts(
        settings,
        output_dir=output_dir if output_dir is not None else settings.model_artifact_dir,
        as_of=timestamp,
    )


def manifest_differences(rebuilt: ServingManifest, committed: ServingManifest) -> list[str]:
    """Manifest fields on which a rebuild disagrees with a committed bundle.

    Comparing the whole manifest rather than only the two artifact hashes
    means a bundle that was published for the wrong tenant, against a stale
    feature contract, or at a different ``as_of`` is caught by the same gate.
    """
    left = rebuilt.to_dict()
    right = committed.to_dict()
    return sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))


def check_serving_artifacts(
    settings: Settings,
    *,
    committed_dir: Path,
    as_of: datetime,
) -> list[str]:
    """Rebuild into a scratch directory and diff against a committed bundle.

    ``ServingManifest.load`` re-hashes the committed artifact files before the
    rebuild starts, so a bundle whose ``ranker.txt`` was edited after its
    manifest was written fails here rather than at sidecar boot.
    """
    committed = ServingManifest.load(committed_dir / settings.model_manifest_name)
    with TemporaryDirectory(prefix="serving-artifacts-check-") as scratch:
        rebuilt = train_serving_artifacts(settings, output_dir=Path(scratch), as_of=as_of)
    return manifest_differences(rebuilt, committed)


def parse_as_of(value: str) -> datetime:
    """Parse the release build's pinned timestamp, in UTC.

    A naive timestamp is refused instead of being assumed to be UTC: it would
    be resolved against whatever zone the build host happens to sit in, and two
    hosts disagreeing on ``trained_at`` is exactly what pinning it prevents.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"--as-of must be an ISO-8601 timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"--as-of must carry a UTC offset: {value!r}")
    return parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train and publish the tenant's two-stage serving bundle."
    )
    parser.add_argument(
        "--as-of",
        help=(
            "ISO-8601 timestamp with a UTC offset. Bounds the ratings the "
            "bundle is trained on and becomes the manifest's trained_at. "
            "Defaults to now, which is what the demo stack wants and what a "
            "reproducible release build must never use."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Where to publish the bundle. Defaults to MODEL_ARTIFACT_DIR.",
    )
    parser.add_argument(
        "--train-only",
        action="store_true",
        help=(
            "Skip Feast materialization. A bundle rebuild is a pure read of "
            "the ratings table; without this it would also append another "
            "generation of feature rows."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Rebuild into a scratch directory and fail if the bundle already "
            "in --output-dir differs. Never materializes and never writes."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check and args.as_of is None:
        parser.error("--check requires the --as-of the committed bundle was built with")

    try:
        as_of = parse_as_of(args.as_of) if args.as_of else datetime.now(UTC)
    except ValueError as error:
        parser.error(str(error))

    settings = Settings()
    output_dir = args.output_dir if args.output_dir is not None else settings.model_artifact_dir

    if args.check:
        differences = check_serving_artifacts(settings, committed_dir=output_dir, as_of=as_of)
        if differences:
            raise SystemExit(
                f"the committed serving bundle in {output_dir} is stale; "
                f"rebuild it with `make serving-artifacts`. "
                f"Differing manifest fields: {', '.join(differences)}"
            )
        logger.info("Serving bundle in %s reproduces at as-of %s", output_dir, as_of.isoformat())
        return

    if args.train_only:
        manifest = train_serving_artifacts(settings, output_dir=output_dir, as_of=as_of)
    else:
        manifest = materialize_and_train(settings, as_of=as_of, output_dir=output_dir)
    logger.info(
        "Published serving artifacts tenant=%s candidate=%s ranker=%s features=%s trained_at=%s",
        manifest.tenant_id,
        manifest.candidate.version,
        manifest.ranker.version,
        manifest.feature_version,
        manifest.trained_at,
    )


if __name__ == "__main__":
    main()
