"""Feature-engineering module consumed by the LightGBM ranker.

Two paths live here and share one contract. ``pipeline.py`` is the
in-process, point-in-time-correct path the ranker training scripts use
(``src/training/ranker.py``, ``src/training/demo_artifacts.py``). The
Feast side per ADR 0009 sits alongside it — feature views in
``feast_repo/``, snapshot materialization in ``materialize.py``, and
tenant-scoped online reads in ``online.py``. Both sides are pinned to
``FEATURE_COLUMNS`` from ``src/feature_contract.py``, and the
feature-parity test under ``tests/feature_parity/`` is what keeps the
offline computation and the served values honest with each other.
"""

from src.feature_contract import FEATURE_COLUMNS

from .pipeline import FeatureIndex, build_features

__all__ = ["FEATURE_COLUMNS", "FeatureIndex", "build_features"]
