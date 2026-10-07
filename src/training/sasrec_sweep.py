"""Run reproducible SASRec configuration grids against one loaded dataset."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import mlflow

from src.config import Settings
from src.models.candidates.sasrec import SASRecConfig
from src.training.candidate_data import INPUT_DIR_ENV_VAR, load_inputs
from src.training.sasrec import run_once

logger = logging.getLogger(__name__)


def parse_grid(spec: dict[str, Any]) -> tuple[float, list[tuple[str, SASRecConfig]]]:
    fields = {field.name for field in dataclasses.fields(SASRecConfig)}
    cells: list[tuple[str, SASRecConfig]] = []
    for index, raw in enumerate(spec["cells"]):
        overrides = {key: value for key, value in raw.items() if key != "label"}
        unknown = set(overrides) - fields
        if unknown:
            raise ValueError(f"cell {index} sets unknown SASRecConfig fields: {sorted(unknown)}")
        config = SASRecConfig(**overrides)
        # Validate before any data loads: a misspelled ``training_objective`` in
        # a cells JSON should fail in a second, not after a 25M-row read.
        try:
            config.validate()
        except ValueError as error:
            raise ValueError(f"cell {index} is invalid: {error}") from error
        cells.append((str(raw.get("label", f"cell{index}")), config))
    return float(spec.get("sample_fraction", 1.0)), cells


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    spec = json.loads(Path(args[0]).read_text())
    sample_fraction, cells = parse_grid(spec)
    if spec.get("hold"):
        # A cells file can be committed before its run is decided (WO-3 writes
        # the seed repeats and the full run ahead of the pilots that choose
        # their settings). It runs only once the hold is removed in a commit.
        logger.error("cells file %s is on hold and was not run: %s", args[0], spec["hold"])
        return 3
    settings = Settings()
    input_dir_raw = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
    ratings, _movies = load_inputs(
        settings, input_dir=Path(input_dir_raw) if input_dir_raw else None
    )
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    failures = 0
    for label, config in cells:
        try:
            run_once(ratings, config, sample_fraction=sample_fraction, run_label=label)
        except Exception:
            failures += 1
            logger.exception("SASRec cell %s failed; continuing", label)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
