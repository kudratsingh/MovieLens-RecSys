"""Feature-engineering module consumed by the LightGBM ranker.

Provisional home per ADR 0005 — Phase 3 replaces this with Feast feature
views + an online store. Function signatures are kept stable so Feast can
be a drop-in without touching the ranker training script.
"""

from .pipeline import FEATURE_COLUMNS, FeatureIndex, build_features

__all__ = ["FEATURE_COLUMNS", "FeatureIndex", "build_features"]
