"""Tenant-safe Feast access used by materialization and serving."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from feast import FeatureStore

from src.config import Settings

USER_FEATURES = [
    "user_features:user_interaction_count",
    "user_features:user_days_active",
    "user_features:user_days_since_last_interaction",
]


def configure_feast_environment(settings: Settings) -> None:
    """Populate only Feast's documented environment-backed config values."""
    values = {
        "FEAST_POSTGRES_HOST": settings.admin_user_db_host,
        "FEAST_POSTGRES_PORT": str(settings.admin_user_db_port),
        "FEAST_POSTGRES_DB": settings.admin_user_db_name,
        "FEAST_POSTGRES_USER": settings.admin_user_db_user,
        "FEAST_POSTGRES_PASSWORD": settings.admin_user_db_password,
        "REDIS_CONNECTION_STRING": settings.redis_connection_string,
    }
    for name, value in values.items():
        os.environ.setdefault(name, value)


def create_feature_store(settings: Settings) -> FeatureStore:
    configure_feast_environment(settings)
    return FeatureStore(repo_path=str(Path(settings.feast_repo_path)))


def get_online_user_features(
    store: FeatureStore, *, tenant_id: str, user_id: int
) -> dict[str, Any]:
    """Read a user only with its tenant key; there is no unscoped API."""
    response = store.get_online_features(
        features=USER_FEATURES,
        entity_rows=[{"tenant_id": tenant_id, "user_id": user_id}],
    ).to_dict()
    return {name.rsplit(":", 1)[-1]: response[name.rsplit(":", 1)[-1]][0] for name in USER_FEATURES}
