from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from src.config import Settings
from src.features import FEATURE_COLUMNS, FeatureIndex
from src.features.online import RANKER_FEATURES, create_feature_store


@pytest.mark.parametrize(
    ("view", "entities", "features"),
    [
        (
            "user_features",
            ["tenant_id", "user_id"],
            ["user_interaction_count", "user_days_active", "user_days_since_last_interaction"],
        ),
        (
            "item_features",
            ["tenant_id", "item_id"],
            [
                "item_popularity_all_time",
                "item_popularity_30d",
                "item_popularity_7d",
                "item_age_days",
            ],
        ),
        ("user_item_features", ["tenant_id", "user_id", "item_id"], ["user_genre_affinity"]),
    ],
)
def test_online_values_match_historical_snapshot(
    view: str, entities: list[str], features: list[str]
) -> None:
    settings = Settings()
    engine = create_engine(settings.admin_user_database_url)
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(f"SELECT * FROM feature_store.{view} " "ORDER BY event_timestamp DESC LIMIT 1")
            )
            .mappings()
            .one()
        )
    engine.dispose()

    entity_row = {name: row[name] for name in entities}
    store = create_feature_store(settings)
    feature_refs = [f"{view}:{name}" for name in features]
    online = store.get_online_features(features=feature_refs, entity_rows=[entity_row]).to_dict()
    historical_entities = pd.DataFrame(
        [{**entity_row, "event_timestamp": row["event_timestamp"] + timedelta(seconds=1)}]
    )
    historical = store.get_historical_features(
        entity_df=historical_entities, features=feature_refs
    ).to_df()

    for feature in features:
        assert online[feature][0] == pytest.approx(historical.iloc[0][feature])


def test_online_user_keys_are_tenant_isolated() -> None:
    store = create_feature_store(Settings())
    response = store.get_online_features(
        features=["user_features:user_interaction_count"],
        entity_rows=[{"tenant_id": "not-demo", "user_id": 1001}],
    ).to_dict()
    assert response["user_interaction_count"] == [None]


def test_python_training_features_match_feast_snapshot_and_online_values() -> None:
    """Prove parity across the actual training and serving implementations.

    Feast's historical and online APIs both read the persisted snapshot. The
    ranker deliberately computes arbitrary historical rows with FeatureIndex,
    so this test adds the missing boundary: at a materialization timestamp the
    Python computation must equal the snapshot Feast exposes on both paths.
    """
    settings = Settings()
    engine = create_engine(settings.admin_user_database_url)
    with engine.connect() as connection:
        entity = (
            connection.execute(
                text(
                    "SELECT tenant_id, user_id, item_id, event_timestamp "
                    "FROM feature_store.user_item_features "
                    "ORDER BY event_timestamp DESC LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
        ratings = pd.read_sql(
            text(
                'SELECT "userId", "movieId", rating, timestamp FROM ratings '
                "WHERE tenant_id = :tenant_id"
            ),
            connection,
            params={"tenant_id": entity["tenant_id"]},
        )
        movies = pd.read_sql('SELECT "movieId", genres FROM movies', connection)
    engine.dispose()

    snapshot_time = entity["event_timestamp"]
    query = pd.DataFrame(
        {
            "userId": [entity["user_id"]],
            "movieId": [entity["item_id"]],
            "as_of_timestamp": [int(snapshot_time.timestamp())],
        }
    )
    computed = FeatureIndex.build(ratings, movies).features_for(query).iloc[0]

    entity_row = {
        "tenant_id": entity["tenant_id"],
        "user_id": entity["user_id"],
        "item_id": entity["item_id"],
    }
    store = create_feature_store(settings)
    online = store.get_online_features(
        features=RANKER_FEATURES,
        entity_rows=[entity_row],
    ).to_dict()
    historical = store.get_historical_features(
        entity_df=pd.DataFrame(
            [{**entity_row, "event_timestamp": snapshot_time + timedelta(seconds=1)}]
        ),
        features=RANKER_FEATURES,
    ).to_df()

    for feature in FEATURE_COLUMNS:
        assert computed[feature] == pytest.approx(historical.iloc[0][feature])
        assert computed[feature] == pytest.approx(online[feature][0])
