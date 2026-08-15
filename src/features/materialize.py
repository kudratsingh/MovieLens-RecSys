"""Build current offline snapshots and publish the same rows to Feast/Redis."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from src.config import Settings
from src.features.online import create_feature_store

logger = logging.getLogger(__name__)

_USER_SQL = text("""
    SELECT tenant_id, "userId" AS user_id,
           COUNT(*)::BIGINT AS user_interaction_count,
           (MAX(timestamp) - MIN(timestamp)) / 86400.0 AS user_days_active,
           (:as_of_epoch - MAX(timestamp)) / 86400.0 AS user_days_since_last_interaction
    FROM ratings
    WHERE timestamp < :as_of_epoch
    GROUP BY tenant_id, "userId"
""")

_ITEM_SQL = text("""
    SELECT tenant_id, "movieId" AS item_id,
           COUNT(*)::BIGINT AS item_popularity_all_time,
           COUNT(*) FILTER (WHERE timestamp >= :as_of_epoch - 2592000)::BIGINT
               AS item_popularity_30d,
           COUNT(*) FILTER (WHERE timestamp >= :as_of_epoch - 604800)::BIGINT
               AS item_popularity_7d,
           (:as_of_epoch - MIN(timestamp)) / 86400.0 AS item_age_days
    FROM ratings
    WHERE timestamp < :as_of_epoch
    GROUP BY tenant_id, "movieId"
""")

_USER_ITEM_SQL = text("""
    WITH user_counts AS (
        SELECT tenant_id, "userId" AS user_id, COUNT(*)::DOUBLE PRECISION AS total
        FROM ratings WHERE timestamp < :as_of_epoch GROUP BY tenant_id, "userId"
    ), candidates AS (
        SELECT u.tenant_id, u.user_id, m."movieId" AS item_id,
               string_to_array(m.genres, '|') AS genres, u.total
        FROM user_counts u CROSS JOIN movies m
    )
    SELECT c.tenant_id, c.user_id, c.item_id,
           COUNT(r.*) FILTER (
               WHERE string_to_array(seen.genres, '|') && c.genres
           )::DOUBLE PRECISION / c.total AS user_genre_affinity
    FROM candidates c
    LEFT JOIN ratings r ON r.tenant_id = c.tenant_id AND r."userId" = c.user_id
                       AND r.timestamp < :as_of_epoch
    LEFT JOIN movies seen ON seen."movieId" = r."movieId"
    GROUP BY c.tenant_id, c.user_id, c.item_id, c.total
""")


def build_snapshots(engine: Engine, *, as_of: datetime) -> dict[str, pd.DataFrame]:
    """Compute all Phase 2 features from interactions strictly before ``as_of``."""
    as_of = as_of.astimezone(UTC)
    params = {"as_of_epoch": int(as_of.timestamp())}
    with engine.connect() as connection:
        frames = {
            "user_features": pd.read_sql(_USER_SQL, connection, params=params),
            "item_features": pd.read_sql(_ITEM_SQL, connection, params=params),
            "user_item_features": pd.read_sql(_USER_ITEM_SQL, connection, params=params),
        }
    for frame in frames.values():
        frame["event_timestamp"] = as_of
    return frames


def materialize(settings: Settings, *, as_of: datetime | None = None) -> dict[str, int]:
    """Persist one historical snapshot and publish those exact frames online."""
    timestamp = as_of or datetime.now(UTC)
    engine = create_engine(settings.admin_user_database_url, future=True)
    try:
        frames = build_snapshots(engine, as_of=timestamp)
        with engine.begin() as connection:
            for table, frame in frames.items():
                frame.to_sql(
                    table,
                    connection,
                    schema="feature_store",
                    if_exists="append",
                    index=False,
                    method="multi",
                )
        store = create_feature_store(settings)
        from src.features.feast_repo.features import (
            item,
            item_features,
            tenant,
            user,
            user_features,
            user_item_features,
        )

        store.apply([tenant, user, item, user_features, item_features, user_item_features])
        for view_name, frame in frames.items():
            store.write_to_online_store(view_name, df=frame)
        return {name: len(frame) for name, frame in frames.items()}
    finally:
        engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = materialize(Settings())
    logger.info("Materialized Feast snapshots: %s", counts)


if __name__ == "__main__":
    main()
