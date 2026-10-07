"""Prove a device can train SASRec, and that what it trains is scored on the CPU (WO-5).

``python -m src.training.gpu_smoke --device cuda --out gpu-smoke.json``

``resolve_device("cuda")`` had never run on an NVIDIA card before WO-5's rented
session, so the session runs this first and stops if any check fails: two minutes
here is cheaper than a timing, or later a sweep, built on a device path nobody has
exercised. The same command runs with ``--device mps`` on the Mac and with
``--device cpu`` in the unit suite, so the path is tested before a pod exists.

The checks:

- the device is there, and ``resolve_device`` hands back that device;
- v1's shape (64 wide, 2 blocks, 2 heads, history 50, BCE with 32 negatives,
  the strict-prefix objective) trains for exactly 200 optimizer steps with every
  parameter on the device at every step, finite losses, and a loss that falls;
- after ``fit`` every parameter is back on the CPU (ADR 0020's D6: everything a
  model scores is computed on the CPU);
- it memorizes: users walk a 60-movie cycle, so the next movie is a function of
  the last one, and recall@10 through ``src.evaluation`` must come out near 1
  (chance is 0.17). This is the ADR 0020 memorization check at v1's shape, with
  dropout off as the repository's memorization tests run it: at 200 steps,
  dropout 0.2 leaves a working model near chance on a 9-movie history, so the
  check would measure dropout rather than the device;
- the timing cells' loss, sampled softmax over 1,024 negatives, trains on the
  device with finite losses (on a 1,500-movie walk, since it needs the room);
- the model exported on this machine reloads on the CPU with the same weights
  (``encoder_weights_sha256``) and the same top-10 lists: the archive path every
  D6 calibration number will be scored through;
- the trained encoder gives the same encodings on the device as on the CPU, to
  within ``FORWARD_PARITY_TOLERANCE``.

It writes one JSON document and exits 1 if any check failed. Nothing here touches
MLflow, the MovieLens data or the network.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import statistics
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch

from src.evaluation.protocol import evaluate
from src.models.candidates.sasrec import (
    DEVICES,
    LEGACY_TRAINING_OBJECTIVE,
    SASRecConfig,
    SASRecModel,
    SASRecTrainingStep,
    resolve_device,
)
from src.models.candidates.sasrec_artifact import MANIFEST_FILENAME, export_sasrec, load_sasrec
from src.models.candidates.sequence_data import build_user_history
from src.training.sasrec import encoder_weights_sha256
from src.training.sasrec_timing import device_info, git_info, host_info, software_info

logger = logging.getLogger(__name__)

SMOKE_KIND = "gpu-smoke"
SCHEMA_VERSION = 1
# The memorization frame: 128 users, each 9 movies along a 60-movie cycle, so 8
# strict-prefix examples each. 1,024 examples at 128 per step is 8 steps a pass,
# and 25 passes are the 200 steps the check asks for.
MEMORIZATION_USERS = 128
MEMORIZATION_CATALOG = 60
MEMORIZATION_LENGTH = 9
BATCH_SIZE = 128
EPOCHS = 25
EXPECTED_STEPS = MEMORIZATION_USERS * (MEMORIZATION_LENGTH - 1) // BATCH_SIZE * EPOCHS
# The sampled-softmax frame needs a catalog larger than 1,024 negatives plus a history.
WALK_USERS = 200
WALK_CATALOG = 1_500
WALK_LENGTH = 33
MOVIE_ID_OFFSET = 100_000
SEED = 20261007
RECALL_K = 10
# Chance is K / catalog = 0.17; on the Mac (cpu and mps) the check measures 1.0.
MIN_RECALL_AT_K = 0.90
# An eval-mode forward of the same weights on two devices differs by float
# summation order only; mps measured 1.2e-6.
FORWARD_PARITY_TOLERANCE = 1e-3
SAMPLED_SOFTMAX_NEGATIVES = 1_024


@dataclass
class Check:
    name: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


def _frame(rows: list[tuple[int, int, float, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["userId", "movieId", "rating", "timestamp"])


def memorization_frame() -> tuple[pd.DataFrame, dict[int, set[int]]]:
    """Users walk one cycle from different starts; the holdout is the next movie.

    The repository's memorization fixture, sized to v1's shape. Movie ids start
    at ``MOVIE_ID_OFFSET`` so a dense index can never pass for a movie id.
    """
    rows: list[tuple[int, int, float, int]] = []
    holdout: dict[int, set[int]] = {}
    for user in range(1, MEMORIZATION_USERS + 1):
        start = (user * 3) % MEMORIZATION_CATALOG
        walk = [
            MOVIE_ID_OFFSET + (start + offset) % MEMORIZATION_CATALOG
            for offset in range(MEMORIZATION_LENGTH + 1)
        ]
        rows.extend((user, movie, 4.0, 1_600_000_000 + 60 * i) for i, movie in enumerate(walk[:-1]))
        holdout[user] = {walk[-1]}
    return _frame(rows), holdout


def walk_frame() -> pd.DataFrame:
    """Longer walks over 1,500 movies from seeded random starts, for the sampled softmax."""
    rng = np.random.default_rng(SEED)
    rows: list[tuple[int, int, float, int]] = []
    for user in range(1, WALK_USERS + 1):
        start = int(rng.integers(0, WALK_CATALOG))
        rows.extend(
            (user, MOVIE_ID_OFFSET + (start + i) % WALK_CATALOG, 4.0, 1_600_000_000 + 60 * i)
            for i in range(WALK_LENGTH)
        )
    return _frame(rows)


def v1_shape(device: str, **changes: Any) -> SASRecConfig:
    """v1's cell (run 528b1451's shape and loss) at the smoke's batch, passes and dropout."""
    base = SASRecConfig(
        max_sequence_length=50,
        hidden_dim=64,
        num_blocks=2,
        num_heads=2,
        feedforward_dim=256,
        dropout=0.0,
        loss="bce",
        negative_count=32,
        batch_size=BATCH_SIZE,
        epochs=EPOCHS,
        learning_rate=1e-3,
        faiss_exact=True,
        seed=42,
        training_objective=LEGACY_TRAINING_OBJECTIVE,
        device=device,
    )
    return SASRecConfig(**{**asdict(base), **changes})


@dataclass
class _FitTrace:
    devices: list[str] = field(default_factory=list)
    losses: list[float] = field(default_factory=list)

    def recorder(self, model: SASRecModel) -> Callable[[SASRecTrainingStep], None]:
        def on_step(step: SASRecTrainingStep) -> None:
            assert model._encoder is not None
            self.devices.append(
                ",".join(sorted({p.device.type for p in model._encoder.parameters()}))
            )
            self.losses.append(step.loss)

        return on_step


def _fit(config: SASRecConfig, frame: pd.DataFrame) -> tuple[SASRecModel, _FitTrace, float]:
    model = SASRecModel(config=config, cold_start_threshold=None)
    trace = _FitTrace()
    started = time.perf_counter()
    model.fit(frame, on_step=trace.recorder(model), retrieval_backend="torch")
    return model, trace, time.perf_counter() - started


def _parameter_devices(model: SASRecModel) -> set[str]:
    assert model._encoder is not None
    return {parameter.device.type for parameter in model._encoder.parameters()}


def _recall(model: SASRecModel, frame: pd.DataFrame, holdout: dict[int, set[int]]) -> float:
    recommendations = model.recommend_for_users(sorted(holdout), RECALL_K)
    counts = frame.groupby("userId").size().to_dict()
    return float(evaluate(recommendations, holdout, counts, k=RECALL_K).overall.recall)


def run_smoke(device: str, *, work_dir: Path) -> dict[str, Any]:
    """Every check, in order; the document is complete whether or not they pass."""
    started = time.perf_counter()
    checks: list[Check] = []
    seconds: dict[str, float] = {}

    available = {
        "cpu": True,
        "mps": bool(torch.backends.mps.is_available()),
        "cuda": bool(torch.cuda.is_available()),
    }[device]
    checks.append(Check("device_available", available, {"device": device}))
    if not available:
        return _document(device, checks, seconds, started)
    try:
        _run_checks(device, checks, seconds, work_dir)
    except Exception as error:
        # A CUDA error or a driver mismatch surfaces as an exception, not as a
        # failed comparison. It is recorded as a check so the JSON still comes
        # back from the pod and says where it stopped.
        logger.exception("gpu smoke stopped on an error")
        checks.append(
            Check(
                "completed_without_error",
                False,
                {"error": repr(error), "after_check": checks[-1].name if checks else None},
            )
        )
    return _document(device, checks, seconds, started)


def _run_checks(
    device: str, checks: list[Check], seconds: dict[str, float], work_dir: Path
) -> None:
    resolved = resolve_device(device)
    checks.append(Check("resolve_device", resolved.type == device, {"resolved": str(resolved)}))

    frame, holdout = memorization_frame()
    config = v1_shape(device)
    model, trace, seconds["v1_fit"] = _fit(config, frame)
    on_device = set(trace.devices) == {device}
    checks.append(
        Check(
            "v1_trains_on_device",
            on_device and len(trace.losses) == EXPECTED_STEPS,
            {
                "steps": len(trace.losses),
                "expected_steps": EXPECTED_STEPS,
                "devices": sorted(set(trace.devices)),
            },
        )
    )
    finite = bool(trace.losses) and all(math.isfinite(value) for value in trace.losses)
    head = statistics.fmean(trace.losses[:20]) if trace.losses else math.nan
    tail = statistics.fmean(trace.losses[-20:]) if trace.losses else math.nan
    checks.append(
        Check(
            "v1_loss_finite_and_falling",
            finite and tail < head,
            {"first_20_mean": head, "last_20_mean": tail, "all_finite": finite},
        )
    )
    checks.append(
        Check(
            "back_on_cpu_after_fit",
            _parameter_devices(model) == {"cpu"},
            {"devices": sorted(_parameter_devices(model))},
        )
    )
    started_eval = time.perf_counter()
    recall = _recall(model, frame, holdout)
    seconds["v1_eval"] = time.perf_counter() - started_eval
    checks.append(
        Check(
            "v1_memorizes",
            recall >= MIN_RECALL_AT_K,
            {
                f"recall_at_{RECALL_K}": recall,
                "minimum": MIN_RECALL_AT_K,
                "chance": RECALL_K / MEMORIZATION_CATALOG,
            },
        )
    )

    sampled_config = v1_shape(
        device,
        loss="sampled-softmax",
        negative_count=SAMPLED_SOFTMAX_NEGATIVES,
        batch_size=512,
        epochs=1,
    )
    _sampled, sampled_trace, seconds["sampled_softmax_fit"] = _fit(sampled_config, walk_frame())
    sampled_finite = bool(sampled_trace.losses) and all(
        math.isfinite(value) for value in sampled_trace.losses
    )
    checks.append(
        Check(
            "sampled_softmax_1024_on_device",
            sampled_finite and set(sampled_trace.devices) == {device},
            {
                "steps": len(sampled_trace.losses),
                "devices": sorted(set(sampled_trace.devices)),
                "first_loss": sampled_trace.losses[0] if sampled_trace.losses else None,
                "last_loss": sampled_trace.losses[-1] if sampled_trace.losses else None,
            },
        )
    )

    started_round_trip = time.perf_counter()
    checks.append(_round_trip(model, frame, holdout, work_dir))
    seconds["export_reload"] = time.perf_counter() - started_round_trip
    checks.append(_forward_parity(model, device))


def _round_trip(
    model: SASRecModel, frame: pd.DataFrame, holdout: dict[int, set[int]], work_dir: Path
) -> Check:
    """Export here, reload on the CPU, and score both the same way (FAISS exact).

    An archive carries no user histories (serving encodes the history it is
    sent), so the reloaded model is given the same training histories the way
    the guardrail scorer gives them, and then asked for the same users.
    """
    assert model._encoder is not None
    model.build_index()
    before = model.recommend_for_users(sorted(holdout), RECALL_K)
    manifest = export_sasrec(model, work_dir / "smoke-model")
    loaded = load_sasrec(work_dir / "smoke-model" / MANIFEST_FILENAME)
    assert loaded._encoder is not None
    loaded._user_history = build_user_history(frame, loaded._item_to_index)
    after = loaded.recommend_for_users(sorted(holdout), RECALL_K)
    weights_before = encoder_weights_sha256(model._encoder.state_dict())
    weights_after = encoder_weights_sha256(loaded._encoder.state_dict())
    changed_lists = sum(1 for user in before if before[user] != after[user])
    return Check(
        "export_reload_on_cpu",
        weights_before == weights_after
        and changed_lists == 0
        and _parameter_devices(loaded) == {"cpu"},
        {
            "archive_sha256": manifest.model_sha256,
            "weights_sha256": weights_before,
            "reloaded_weights_sha256": weights_after,
            "changed_top_k_lists": changed_lists,
            "users": len(before),
        },
    )


def _forward_parity(model: SASRecModel, device: str) -> Check:
    """The trained weights encode a fixed batch the same on the device and on the CPU."""
    assert model._encoder is not None
    cpu_encoder = model._encoder.eval()
    generator = torch.Generator().manual_seed(SEED)
    n_rows = len(model._index_to_item)
    batch = torch.randint(
        1, n_rows + 1, (64, cpu_encoder.config.max_sequence_length), generator=generator
    )
    batch[:8, :20] = 0  # some left padding, as real histories have
    with torch.no_grad():
        on_cpu = cpu_encoder.encode_positions(batch)
        device_encoder = copy.deepcopy(cpu_encoder).to(device).eval()
        on_device = device_encoder.encode_positions(batch.to(device)).to("cpu")
    difference = float((on_cpu - on_device).abs().max().item())
    return Check(
        "forward_parity_device_vs_cpu",
        difference <= FORWARD_PARITY_TOLERANCE,
        {"max_abs_difference": difference, "tolerance": FORWARD_PARITY_TOLERANCE},
    )


def _document(
    device: str, checks: list[Check], seconds: dict[str, float], started: float
) -> dict[str, Any]:
    seconds["total"] = time.perf_counter() - started
    return {
        "kind": SMOKE_KIND,
        "schema_version": SCHEMA_VERSION,
        "result_status": "smoke test - not a result of record",
        "device": device,
        "device_info": device_info(device),
        "software": software_info(),
        "host": host_info(),
        "git": git_info(),
        "datasets": {
            "memorization": {
                "users": MEMORIZATION_USERS,
                "catalog": MEMORIZATION_CATALOG,
                "train_length": MEMORIZATION_LENGTH,
                "batch_size": BATCH_SIZE,
                "epochs": EPOCHS,
            },
            "sampled_softmax_walk": {
                "users": WALK_USERS,
                "catalog": WALK_CATALOG,
                "train_length": WALK_LENGTH,
                "seed": SEED,
            },
        },
        "checks": [asdict(check) for check in checks],
        "seconds": seconds,
        "passed": all(check.passed for check in checks),
    }


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="python -m src.training.gpu_smoke")
    parser.add_argument("--device", choices=DEVICES, default="cuda")
    parser.add_argument("--out", type=Path, required=True, help="where to write the JSON")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="gpu-smoke-") as scratch:
        document = run_smoke(args.device, work_dir=Path(scratch))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for check in document["checks"]:
        logger.info(
            "%-34s %s %s", check["name"], "PASS" if check["passed"] else "FAIL", check["detail"]
        )
    logger.info(
        "gpu smoke on %s: %s (%.1f s)",
        args.device,
        "PASS" if document["passed"] else "FAIL",
        document["seconds"]["total"],
    )
    return 0 if document["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
