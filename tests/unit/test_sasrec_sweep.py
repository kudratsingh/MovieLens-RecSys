from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src.training.sasrec_sweep import parse_grid


def test_committed_pilot_is_valid_and_changes_only_loss() -> None:
    spec = json.loads(Path("docs/experiments/sasrec/pilot.json").read_text())
    fraction, cells = parse_grid(spec)
    assert fraction == 0.005
    assert [label for label, _config in cells] == ["bce-neg32", "gbce-t0.5-neg32"]
    assert cells[0][1].loss == "bce"
    assert cells[1][1].loss == "gbce"
    for field in dataclasses.fields(cells[0][1]):
        if field.name not in {"loss", "calibration_t"}:
            assert getattr(cells[0][1], field.name) == getattr(cells[1][1], field.name)


def test_unknown_grid_field_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown"):
        parse_grid({"cells": [{"label": "bad", "layers": 2}]})
