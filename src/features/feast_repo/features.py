"""Feast declarations for the Phase 2 ranker feature contract."""

from datetime import timedelta

from feast import Entity, FeatureView, Field
from feast.infra.offline_stores.contrib.postgres_offline_store.postgres_source import (
    PostgreSQLSource,
)
from feast.types import Float64, Int64
from feast.value_type import ValueType

tenant = Entity(name="tenant", join_keys=["tenant_id"], value_type=ValueType.STRING)
user = Entity(name="user", join_keys=["user_id"], value_type=ValueType.INT64)
item = Entity(name="item", join_keys=["item_id"], value_type=ValueType.INT64)

user_source = PostgreSQLSource(
    name="user_features_source",
    table="feature_store.user_features",
    timestamp_field="event_timestamp",
)
item_source = PostgreSQLSource(
    name="item_features_source",
    table="feature_store.item_features",
    timestamp_field="event_timestamp",
)
user_item_source = PostgreSQLSource(
    name="user_item_features_source",
    table="feature_store.user_item_features",
    timestamp_field="event_timestamp",
)

user_features = FeatureView(
    name="user_features",
    entities=[tenant, user],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="user_interaction_count", dtype=Int64),
        Field(name="user_days_active", dtype=Float64),
        Field(name="user_days_since_last_interaction", dtype=Float64),
    ],
    source=user_source,
)

item_features = FeatureView(
    name="item_features",
    entities=[tenant, item],
    ttl=timedelta(days=3650),
    schema=[
        Field(name="item_popularity_all_time", dtype=Int64),
        Field(name="item_popularity_30d", dtype=Int64),
        Field(name="item_popularity_7d", dtype=Int64),
        Field(name="item_age_days", dtype=Float64),
    ],
    source=item_source,
)

user_item_features = FeatureView(
    name="user_item_features",
    entities=[tenant, user, item],
    ttl=timedelta(days=3650),
    schema=[Field(name="user_genre_affinity", dtype=Float64)],
    source=user_item_source,
)
