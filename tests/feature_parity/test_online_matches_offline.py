from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from src.config import Settings
from src.features.online import create_feature_store


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
