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


def test_established_pilot_uses_same_exact_loss_ablation() -> None:
    spec = json.loads(Path("docs/experiments/sasrec/pilot-6pct.json").read_text())
    fraction, cells = parse_grid(spec)
    assert fraction == 0.06
    assert all(config.faiss_exact for _label, config in cells)
    assert [config.loss for _label, config in cells] == ["bce", "gbce"]


def test_full_run_freezes_winning_pilot_cell() -> None:
    spec = json.loads(Path("docs/experiments/sasrec/full.json").read_text())
    fraction, cells = parse_grid(spec)
    assert fraction == 1.0
    assert len(cells) == 1
    label, config = cells[0]
    assert label == "full-bce-neg32"
    assert config.loss == "bce"
    assert config.negative_count == 32
    assert config.epochs == 2
    assert config.faiss_exact is True
    assert config.seed == 42


@pytest.mark.parametrize(
    ("path", "fraction", "label"),
    [
        (
            "docs/experiments/sasrec/all-positions-pilot-6pct.json",
            0.06,
            "allpos-pilot6-bce-neg32",
        ),
        (
            "docs/experiments/sasrec/all-positions-full.json",
            1.0,
            "allpos-full-bce-neg32",
        ),
    ],
)
def test_all_position_measurements_hold_the_v1_cell_fixed(
    path: str, fraction: float, label: str
) -> None:
    v1_spec = json.loads(Path("docs/experiments/sasrec/full.json").read_text())
    _v1_fraction, v1_cells = parse_grid(v1_spec)
    spec = json.loads(Path(path).read_text())
    measured_fraction, cells = parse_grid(spec)

    assert measured_fraction == fraction
    assert len(cells) == 1
    assert cells[0][0] == label
    assert cells[0][1] == v1_cells[0][1]


def test_unknown_grid_field_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown"):
        parse_grid({"cells": [{"label": "bad", "layers": 2}]})
