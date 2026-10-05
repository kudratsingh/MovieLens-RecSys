from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src.models.candidates.sasrec import (
    ALL_POSITION_TRAINING_OBJECTIVE,
    LEGACY_TRAINING_OBJECTIVE,
)
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
    # The objective is the one axis these measurements changed, and since WO-1
    # the file has to say so: the default is the objective of record again.
    assert cells[0][1].training_objective == ALL_POSITION_TRAINING_OBJECTIVE
    assert v1_cells[0][1].training_objective == LEGACY_TRAINING_OBJECTIVE
    assert dataclasses.replace(cells[0][1], training_objective=LEGACY_TRAINING_OBJECTIVE) == (
        v1_cells[0][1]
    )


def test_unknown_grid_field_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown"):
        parse_grid({"cells": [{"label": "bad", "layers": 2}]})


def test_wo1_pilots_hold_the_established_cell_and_vary_only_the_seed() -> None:
    established_spec = json.loads(Path("docs/experiments/sasrec/pilot-6pct.json").read_text())
    _fraction, established_cells = parse_grid(established_spec)
    established = dict(established_cells)["pilot6-bce-neg32"]
    control_spec = json.loads(
        Path("docs/experiments/sasrec/wo1-old-loop-control-6pct.json").read_text()
    )
    control_fraction, control_cells = parse_grid(control_spec)
    restored_cells = []
    for name in ("wo1-restored-loop-6pct-s42.json", "wo1-restored-loop-6pct-seeds.json"):
        restored_fraction, cells = parse_grid(
            json.loads((Path("docs/experiments/sasrec") / name).read_text())
        )
        assert restored_fraction == 0.06
        restored_cells.extend(cells)

    assert control_fraction == 0.06
    # The control must also parse at 89520be^, whose config has no objective field.
    assert all("training_objective" not in cell for cell in control_spec["cells"])
    assert [config for _label, config in control_cells] == [established]
    assert [config.seed for _label, config in restored_cells] == [42, 7, 13, 21]
    assert len({label for label, _config in restored_cells}) == 4
    for _label, config in restored_cells:
        assert config.training_objective == LEGACY_TRAINING_OBJECTIVE
        assert dataclasses.replace(config, seed=established.seed) == established
    assert restored_cells[0][1] == control_cells[0][1]


def test_grid_accepts_either_objective_and_rejects_any_other() -> None:
    _fraction, cells = parse_grid(
        {
            "cells": [
                {"label": "record", "training_objective": LEGACY_TRAINING_OBJECTIVE},
                {"label": "ablation", "training_objective": ALL_POSITION_TRAINING_OBJECTIVE},
            ]
        }
    )
    assert [config.training_objective for _label, config in cells] == [
        LEGACY_TRAINING_OBJECTIVE,
        ALL_POSITION_TRAINING_OBJECTIVE,
    ]
    with pytest.raises(ValueError, match="cell 0 is invalid"):
        parse_grid({"cells": [{"label": "typo", "training_objective": "all-positions"}]})
