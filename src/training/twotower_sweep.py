"""
Run a list of two-tower configurations back to back, one MLflow run each.

This exists for one reason: the 25 M-row ``read_sql`` every trainer opens
with costs 40–460 s depending on what else the host is doing, and a sweep
that pays it per cell spends a meaningful slice of its budget re-reading
data it already has. Everything else — the split, the fit, the metrics, the
MLflow params — is ``src.training.twotower.run_once``, unchanged, so a sweep
cell and a hand-run ``make train-twotower`` produce the same shape of run
and are directly comparable in the same experiment.

The grid is a JSON file, not code, so a sweep is reproducible from an
artifact rather than from a diff::

    {
      "sample_fraction": 0.05,
      "cells": [
        {"label": "lr1e-3-t1.0", "learning_rate": 0.001, "logit_temperature": 1.0},
        {"label": "lr1e-3-t0.05", "learning_rate": 0.001, "logit_temperature": 0.05}
      ]
    }

Each cell's keys are ``TwoTowerConfig`` field names; anything absent takes
the currently accepted ADR 0015 default. ``label`` is the only non-field key and lands in the
MLflow run name and the ``sweep_label`` tag.

Usage::

    python -m src.training.twotower_sweep path/to/grid.json

A cell that raises is logged and the sweep continues — an eight-hour sweep
should not lose its remaining cells to one bad configuration.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import mlflow

from src.config import Settings
from src.models.candidates.twotower import TwoTowerConfig
from src.training.twotower import load_inputs, run_once

logger = logging.getLogger(__name__)

LABEL_KEY = "label"


def parse_grid(spec: dict[str, Any]) -> tuple[float, list[tuple[str, TwoTowerConfig]]]:
    """Turn a grid document into ``(sample_fraction, [(label, config), …])``.

    Unknown config keys are a hard error rather than a silent no-op: a cell
    that names ``temperature`` instead of ``logit_temperature`` would
    otherwise run at the default and be written up as if it had not.
    """
    field_names = {f.name for f in dataclasses.fields(TwoTowerConfig)}
    sample_fraction = float(spec.get("sample_fraction", 1.0))

    cells: list[tuple[str, TwoTowerConfig]] = []
    for i, raw in enumerate(spec["cells"]):
        overrides = {k: v for k, v in raw.items() if k != LABEL_KEY}
        unknown = set(overrides) - field_names
        if unknown:
            raise ValueError(f"cell {i} sets unknown TwoTowerConfig fields: {sorted(unknown)}")
        cells.append((str(raw.get(LABEL_KEY, f"cell{i}")), TwoTowerConfig(**overrides)))
    return sample_fraction, cells


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2

    spec = json.loads(Path(args[0]).read_text())
    sample_fraction, cells = parse_grid(spec)
    logger.info("Sweep: %d cells at sample_fraction=%s", len(cells), sample_fraction)

    settings = Settings()
    ratings = load_inputs(settings)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)

    failures = 0
    for i, (label, config) in enumerate(cells, start=1):
        logger.info("=== cell %d/%d: %s === %s", i, len(cells), label, config)
        t0 = time.perf_counter()
        try:
            run_once(ratings, config, sample_fraction=sample_fraction, run_label=label)
        except Exception:
            failures += 1
            logger.exception("cell %s failed; continuing", label)
        logger.info("=== cell %s done in %.1fs ===", label, time.perf_counter() - t0)

    logger.info("Sweep finished: %d cells, %d failures", len(cells), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
