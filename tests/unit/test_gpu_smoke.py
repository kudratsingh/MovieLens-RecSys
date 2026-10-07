"""The device smoke the rented-GPU session runs first (WO-5 step one).

On ``cpu`` it runs everywhere, CI included; on ``mps`` wherever the Mac's GPU is
there. ``cuda`` itself can only be proved on the pod, which is exactly why the
same code path is exercised here on the devices that exist.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import torch

from src.training import gpu_smoke

MPS = torch.backends.mps.is_available()

EXPECTED_CHECKS = [
    "device_available",
    "resolve_device",
    "v1_trains_on_device",
    "v1_loss_finite_and_falling",
    "back_on_cpu_after_fit",
    "v1_memorizes",
    "sampled_softmax_1024_on_device",
    "export_reload_on_cpu",
    "forward_parity_device_vs_cpu",
]


def _check(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(check for check in document["checks"] if check["name"] == name)


def test_the_smoke_frames_have_the_shape_the_checks_assume() -> None:
    frame, holdout = gpu_smoke.memorization_frame()
    walk = gpu_smoke.walk_frame()

    assert gpu_smoke.EXPECTED_STEPS == 200
    assert len(frame) == gpu_smoke.MEMORIZATION_USERS * gpu_smoke.MEMORIZATION_LENGTH
    assert frame["movieId"].nunique() == gpu_smoke.MEMORIZATION_CATALOG
    assert all(len(targets) == 1 for targets in holdout.values())
    # Room for 1,024 negatives beyond any history.
    assert walk["movieId"].nunique() > 1024 + gpu_smoke.WALK_LENGTH


def test_the_smoke_trains_v1s_shape() -> None:
    config = gpu_smoke.v1_shape("cpu")

    assert (config.hidden_dim, config.num_blocks, config.num_heads) == (64, 2, 2)
    assert (config.max_sequence_length, config.feedforward_dim) == (50, 256)
    assert (config.loss, config.negative_count) == ("bce", 32)


def test_the_smoke_passes_on_the_cpu(tmp_path: Path) -> None:
    document = gpu_smoke.run_smoke("cpu", work_dir=tmp_path)

    assert [check["name"] for check in document["checks"]] == EXPECTED_CHECKS
    assert document["passed"], [c for c in document["checks"] if not c["passed"]]
    assert _check(document, "v1_trains_on_device")["detail"]["steps"] == 200
    assert _check(document, "export_reload_on_cpu")["detail"]["changed_top_k_lists"] == 0
    json.dumps(document)


@pytest.mark.skipif(not MPS, reason="needs the Mac's GPU (torch.backends.mps)")
def test_the_smoke_passes_on_mps(tmp_path: Path) -> None:
    document = gpu_smoke.run_smoke("mps", work_dir=tmp_path)

    assert document["passed"], [c for c in document["checks"] if not c["passed"]]
    assert _check(document, "v1_trains_on_device")["detail"]["devices"] == ["mps"]
    assert _check(document, "back_on_cpu_after_fit")["detail"]["devices"] == ["cpu"]


def test_a_missing_device_fails_the_smoke_and_still_writes_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    out = tmp_path / "smoke.json"

    assert gpu_smoke.main(["--device", "cuda", "--out", str(out)]) == 1
    document = json.loads(out.read_text())
    assert document["passed"] is False
    assert [check["name"] for check in document["checks"]] == ["device_available"]


def test_an_error_mid_smoke_is_a_failed_check_not_a_lost_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(name: str) -> torch.device:
        raise RuntimeError("CUDA error: no kernel image is available for execution")

    monkeypatch.setattr(gpu_smoke, "resolve_device", broken)

    document = gpu_smoke.run_smoke("cpu", work_dir=tmp_path)

    assert document["passed"] is False
    failed = _check(document, "completed_without_error")
    assert "no kernel image" in failed["detail"]["error"]
    assert failed["detail"]["after_check"] == "device_available"
