"""Dependency-free ranker feature schema shared by training and serving."""

from __future__ import annotations

FEATURE_COLUMNS: list[str] = [
    "user_interaction_count",
    "user_days_active",
    "user_days_since_last_interaction",
    "item_popularity_all_time",
    "item_popularity_30d",
    "item_popularity_7d",
    "item_age_days",
    "user_genre_affinity",
]
