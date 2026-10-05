"""WO-4: the trainer upgrades leave v1 alone, and each one does what it says.

The first section is the guarantee the work order asks for — the v1 cell's first
batches and loss are unchanged by WO-4. It is pinned two ways. A frozen copy of
the pre-WO-4 loop, sampler and loss (``git show c07b4e0:src/models/candidates/
sasrec.py``) lives in this file, so the comparison cannot drift with ``src/``; it
runs everywhere. And the values that copy produced on the capture machine before
any WO-4 code existed are pinned as constants, checked wherever the environment
matches the capture (float results depend on the BLAS build and torch version).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import platform
from pathlib import Path
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import pytest
import torch
import torch.nn.functional as F  # noqa: N812
from mlflow.tracking import MlflowClient

import src.models.candidates.sasrec as sasrec
import src.training.sasrec as sasrec_training
from src.models.candidates.sasrec import (
    ALL_POSITION_TRAINING_OBJECTIVE,
    LEGACY_TRAINING_OBJECTIVE,
    POST_RECORD_FIELDS,
    SASRecConfig,
    SASRecEncoder,
    SASRecModel,
    SASRecTrainingStep,
    _window_groups,
    resolve_device,
)
from src.models.candidates.sequence_data import build_strict_prefix_example_store
from src.training.sasrec import _configuration_id, encoder_weights_sha256, run_once
from src.training.sasrec_sweep import parse_grid
from src.training.sasrec_training_log import (
    STEP_GRAD_NORM_METRIC,
    STEP_LOSS_METRIC,
    StepMetricsLogger,
    render_loss_curve,
)

MPS = torch.backends.mps.is_available()

# --- 1. v1 is unchanged --------------------------------------------------------


def _v1_cell() -> SASRecConfig:
    """The v1 cell: 64 wide, 2 blocks, 2 heads, history 50, BCE with 32 negatives."""
    return SASRecConfig(
        loss="bce",
        negative_count=32,
        epochs=2,
        faiss_exact=True,
        seed=42,
        training_objective=LEGACY_TRAINING_OBJECTIVE,
    )


def _v1_frame() -> pd.DataFrame:
    """2,963 examples: six full 512-example steps per epoch, histories from 2 to 120."""
    rng = np.random.default_rng(20261005)
    rows: list[tuple[int, int, int]] = []
    for user in range(1, 51):
        length = int(rng.integers(3, 121))
        watched = rng.choice(np.arange(1000, 1400), size=length, replace=False)
        clock = np.sort(rng.integers(0, max(2, length - 5), size=length))
        rows.extend((user, int(item), int(t)) for item, t in zip(watched, clock))
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    return frame.sample(frac=1.0, random_state=7).reset_index(drop=True)


def _digest(tensor: torch.Tensor) -> str:
    array = np.ascontiguousarray(tensor.detach().cpu().numpy())
    return hashlib.sha256(
        str(array.dtype).encode() + repr(array.shape).encode() + array.tobytes()
    ).hexdigest()


def _frozen_sample_negatives(
    histories: torch.Tensor,
    positives: torch.Tensor,
    *,
    n_items: int,
    count: int,
    rng: np.random.Generator,
) -> torch.Tensor:
    """``sample_negatives`` at c07b4e0, verbatim."""
    output = np.empty((len(positives), count), dtype=np.int64)
    for row, (history, positive) in enumerate(zip(histories.numpy(), positives.numpy())):
        forbidden = set(int(item) for item in history if item)
        forbidden.add(int(positive))
        if n_items - len(forbidden) < count:
            raise ValueError("not enough eligible unique negatives for requested count")
        selected: list[int] = []
        selected_set: set[int] = set()
        while len(selected) < count:
            draws = rng.integers(1, n_items + 1, size=max(8, 2 * (count - len(selected))))
            for candidate_value in draws:
                candidate = int(candidate_value)
                if candidate not in forbidden and candidate not in selected_set:
                    selected.append(candidate)
                    selected_set.add(candidate)
                    if len(selected) == count:
                        break
        output[row] = selected
    return torch.from_numpy(output)


def _frozen_sampled_gbce_loss(
    positive_logits: torch.Tensor, negative_logits: torch.Tensor, *, beta: float
) -> torch.Tensor:
    """``sampled_gbce_loss`` at c07b4e0, verbatim."""
    positive = beta * F.softplus(-positive_logits)
    negative = F.softplus(negative_logits).sum(dim=1)
    return ((positive + negative) / (negative_logits.shape[1] + 1)).mean()


def _frozen_v1_fit(train: pd.DataFrame, config: SASRecConfig) -> dict[str, Any]:
    """``SASRecModel.fit`` plus ``_train_strict_prefix`` at c07b4e0, index build omitted."""
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    items = sorted(int(item) for item in train["movieId"].unique())
    item_to_index = {item: index + 1 for index, item in enumerate(items)}
    examples = build_strict_prefix_example_store(
        train, item_to_index=item_to_index, max_length=config.max_sequence_length
    )
    encoder = SASRecEncoder(len(items) + 2, config)
    optimizer = torch.optim.Adam(encoder.parameters(), lr=config.learning_rate)
    rng = np.random.default_rng(config.seed)
    batches: list[dict[str, str]] = []
    step_losses: list[float] = []
    epoch_losses: list[float] = []
    for _epoch in range(config.epochs):
        encoder.train()
        permutation = torch.randperm(len(examples))
        epoch_loss = 0.0
        count = 0
        for start in range(0, len(examples), config.batch_size):
            rows = permutation[start : start + config.batch_size]
            history_batch, positive_batch = examples.batch(rows)
            negative_batch = _frozen_sample_negatives(
                history_batch,
                positive_batch,
                n_items=len(items),
                count=config.negative_count,
                rng=rng,
            )
            if len(batches) < 3:
                batches.append(
                    {
                        "histories": _digest(history_batch),
                        "positives": _digest(positive_batch),
                        "negatives": _digest(negative_batch),
                    }
                )
            user_vectors = encoder.training_user_vectors(history_batch)
            positive_logits = (
                user_vectors * encoder.item_vectors(positive_batch, normalize=False)
            ).sum(dim=1)
            negative_logits = torch.einsum(
                "bd,bkd->bk",
                user_vectors,
                encoder.item_vectors(negative_batch, normalize=False),
            )
            loss = _frozen_sampled_gbce_loss(positive_logits, negative_logits, beta=1.0)
            optimizer.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            with torch.no_grad():
                encoder.item_embedding.weight[0].zero_()
            epoch_loss += float(loss.item())
            step_losses.append(float(loss.item()))
            count += 1
        epoch_losses.append(epoch_loss / max(1, count))
    return {
        "first_batches": batches,
        "step_losses": step_losses,
        "epoch_losses": epoch_losses,
        "weights_sha256": encoder_weights_sha256(encoder.state_dict()),
    }


def _current_v1_fit(
    train: pd.DataFrame, config: SASRecConfig, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    batches: list[dict[str, str]] = []
    original = sasrec.sample_negatives

    def recorded(histories: torch.Tensor, positives: torch.Tensor, **kwargs: Any) -> torch.Tensor:
        negatives = original(histories, positives, **kwargs)
        if len(batches) < 3:
            batches.append(
                {
                    "histories": _digest(histories),
                    "positives": _digest(positives),
                    "negatives": _digest(negatives),
                }
            )
        return negatives

    monkeypatch.setattr(sasrec, "sample_negatives", recorded)
    steps: list[SASRecTrainingStep] = []
    epoch_losses: list[float] = []
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        train,
        on_epoch=lambda _epoch, loss: epoch_losses.append(loss),
        retrieval_backend="torch",
        on_step=steps.append,
    )
    assert model._encoder is not None
    return {
        "first_batches": batches,
        "step_losses": [step.loss for step in steps],
        "epoch_losses": epoch_losses,
        "weights_sha256": encoder_weights_sha256(model._encoder.state_dict()),
    }


# Captured on 2026-10-05 from c07b4e0 (main with WO-1 and WO-2, before any WO-4 code), with
# torch 2.13.0 / NumPy 2.4.6 on Darwin arm64 and one thread: the first three
# batches, the first three step losses and both epoch losses (as float.hex), and
# the trained weights' digest.
_CAPTURE_ENVIRONMENT = ("2.13.0", "Darwin", "arm64")
_GOLDEN_V1 = {
    "first_batches": [
        {
            "histories": "eaa12f93ac558a89a672005ea66bad155ba587bcd933715053dc85bd77c88005",
            "positives": "e3bf81f7ae4d2d89b1373c4e040873a64d0dc33e929c97d916aaa81cb3fe06bf",
            "negatives": "47b199f782840f00eab19802bc91e77ecd294bc1a7a557933c51d5de0b4dc51d",
        },
        {
            "histories": "399709654115bc506c72bab86d960c61d89fce445def7bb56f6662c89b21e149",
            "positives": "bf0cc9c0dc5e4ccc89317255337f24aefabd4fde43f27b204f66cbbdd5680b39",
            "negatives": "9f0515748355c603b93d9cbcebce30a05432ec16e35025528d133f0a0cc9032f",
        },
        {
            "histories": "d1482340d4280fb3470532f4f8aa7be7f65f8ccb56a775544324593e9985473d",
            "positives": "7cb53f8e2963bb623a600b558c91c36159330e94b3f139c6475d09561e1291b6",
            "negatives": "207dddaeb9073ee56f426d3c3caefdde8646d9c6516883e5b9f761bb39c1ee8b",
        },
    ],
    "first_step_losses": ["0x1.9971100000000p-1", "0x1.84790a0000000p-1", "0x1.651c7a0000000p-1"],
    "n_steps": 12,
    "epoch_losses": ["0x1.61c63caaaaaabp-1", "0x1.0ebeb6aaaaaabp-1"],
    "weights_sha256": "sha256:391d6960c0f354dd755e47acc4894e9205895d01fb711f1b1b5fe39a0264e878",
}


def test_the_v1_cell_trains_exactly_as_the_frozen_pre_wo4_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train = _v1_frame()
    expected = _frozen_v1_fit(train, _v1_cell())
    actual = _current_v1_fit(train, _v1_cell(), monkeypatch)

    assert actual["first_batches"] == expected["first_batches"]
    # Exact equality, not closeness: the same statements in the same order.
    assert actual["step_losses"] == expected["step_losses"]
    assert actual["epoch_losses"] == expected["epoch_losses"]
    assert actual["weights_sha256"] == expected["weights_sha256"]


@pytest.mark.skipif(
    (torch.__version__.split("+")[0], platform.system(), platform.machine())
    != _CAPTURE_ENVIRONMENT,
    reason="float values are pinned for the environment they were captured on",
)
def test_the_v1_cell_reproduces_the_values_captured_before_wo4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = _current_v1_fit(_v1_frame(), _v1_cell(), monkeypatch)

    assert actual["first_batches"] == _GOLDEN_V1["first_batches"]
    assert [loss.hex() for loss in actual["step_losses"][:3]] == _GOLDEN_V1["first_step_losses"]
    assert len(actual["step_losses"]) == _GOLDEN_V1["n_steps"]
    assert [loss.hex() for loss in actual["epoch_losses"]] == _GOLDEN_V1["epoch_losses"]
    assert actual["weights_sha256"] == _GOLDEN_V1["weights_sha256"]


def test_configuration_ids_of_recorded_cells_do_not_move() -> None:
    """Every WO-4 field is left out of the id at its default, so no id changes.

    The values are what ``_configuration_id`` returned for these files at c07b4e0.
    """
    expected = {
        "full.json": "c57774687d8151b9e605084a6eacd3eb5bc4fb32be1af00f9abd08b2af568efa",
        "wo1-restored-loop-6pct-s42.json": (
            "0ddbfffec0457d2f6b7f2b5e39ab79aa8e32e1e233076cef6af199d2d3d3c21d"
        ),
        "all-positions-full.json": (
            "bd14a76386477e077ab2dd632672544945de77d6830a8c4bb09d7f12dff0c6d9"
        ),
    }
    for name, digest in expected.items():
        fraction, cells = parse_grid(
            json.loads((Path("docs/experiments/sasrec") / name).read_text())
        )
        assert _configuration_id(cells[0][1], sample_fraction=fraction) == (
            f"sasrec-sha256:{digest}"
        )
    assert _configuration_id(SASRecConfig()) == (
        "sasrec-sha256:f225b1f8a627773182bd808e07b8a60ca60c8cb9fc08a51033eab31a3b0cdeb8"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"device": "mps"},
        {"microbatch_size": 16},
        {"early_stopping": True},
        {"early_stopping_probe_seed": 7},
        {"loss": "sampled-softmax"},
    ],
)
def test_any_non_default_wo4_setting_is_part_of_the_id(change: dict[str, object]) -> None:
    base = SASRecConfig(batch_size=64, epochs=5)
    assert _configuration_id(base) != _configuration_id(dataclasses.replace(base, **change))


def test_post_record_fields_are_real_fields_with_defaults() -> None:
    names = {field.name for field in dataclasses.fields(SASRecConfig)}
    assert set(POST_RECORD_FIELDS) <= names
    assert SASRecConfig().training_objective == LEGACY_TRAINING_OBJECTIVE
    assert SASRecConfig().device == "cpu"
    assert SASRecConfig().microbatch_size == 0
    assert SASRecConfig().early_stopping is False


# --- 2. batch size is examples per step; accumulation ---------------------------


def _small_config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": 12,
        "hidden_dim": 16,
        "num_blocks": 2,
        "num_heads": 2,
        "feedforward_dim": 32,
        "dropout": 0.0,
        "negative_count": 16,
        "loss": "sampled-softmax",
        "batch_size": 48,
        "epochs": 1,
        "faiss_exact": True,
        "seed": 3,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _small_frame(seed: int = 11, users: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[tuple[int, int, int]] = []
    for user in range(1, users + 1):
        length = int(rng.integers(3, 30))
        watched = rng.choice(np.arange(500, 620), size=length, replace=False)
        clock = np.sort(rng.integers(0, max(2, length - 2), size=length))
        rows.extend((user, int(item), int(t)) for item, t in zip(watched, clock))
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


def _first_step_gradients(
    config: SASRecConfig, monkeypatch: pytest.MonkeyPatch
) -> dict[str, torch.Tensor]:
    captured: dict[str, torch.Tensor] = {}
    original = sasrec._gradient_norm

    def capture(module: torch.nn.Module) -> float:
        if not captured:
            for name, parameter in module.named_parameters():
                assert parameter.grad is not None, name
                captured[name] = parameter.grad.detach().clone()
        return original(module)

    monkeypatch.setattr(sasrec, "_gradient_norm", capture)
    SASRecModel(config=config, cold_start_threshold=None).fit(
        _small_frame(), retrieval_backend="torch"
    )
    return captured


@pytest.mark.parametrize(
    "changes",
    [
        {"loss": "bce"},
        {"loss": "sampled-softmax"},
        {"loss": "full-softmax"},
        {"loss": "sampled-softmax", "training_objective": ALL_POSITION_TRAINING_OBJECTIVE},
        {"loss": "full-softmax", "training_objective": ALL_POSITION_TRAINING_OBJECTIVE},
    ],
)
def test_accumulated_passes_give_the_one_pass_gradient(
    changes: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    whole = _first_step_gradients(_small_config(**changes), monkeypatch)
    monkeypatch.undo()
    split = _first_step_gradients(_small_config(microbatch_size=13, **changes), monkeypatch)

    assert whole.keys() == split.keys()
    for name in whole:
        torch.testing.assert_close(split[name], whole[name], rtol=1e-5, atol=1e-7, msg=name)


def test_a_step_reports_its_examples_not_its_pass_size() -> None:
    steps: list[SASRecTrainingStep] = []
    config = _small_config(microbatch_size=10, epochs=2)
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        _small_frame(), retrieval_backend="torch", on_step=steps.append
    )
    report = model.training_report
    assert report is not None and model._training_stats is not None
    n_examples = model._training_stats.n_targets

    assert report.examples_per_step == 48
    assert report.microbatch_examples == 10
    assert report.gradient_accumulation_steps == 5
    assert len(steps) == report.optimizer_steps == 2 * math.ceil(n_examples / 48)
    assert [step.step for step in steps] == list(range(1, len(steps) + 1))
    assert sum(step.examples for step in steps) == 2 * n_examples
    assert max(step.examples for step in steps) == 48


def test_window_groups_partition_targets_without_splitting_a_window() -> None:
    windows = torch.tensor([0, 0, 0, 1, 2, 2, 2, 2, 2, 3])
    groups = _window_groups(windows, 4, 4)

    assert groups == [
        (slice(0, 2), slice(0, 4)),
        (slice(2, 3), slice(4, 9)),
        (slice(3, 4), slice(9, 10)),
    ]
    assert _window_groups(windows, 4, 0) == [(slice(0, 4), slice(0, 10))]
    with pytest.raises(ValueError, match="window by window"):
        _window_groups(torch.tensor([1, 0, 2]), 3, 1)


@pytest.mark.parametrize(
    "config",
    [
        SASRecConfig(batch_size=0),
        SASRecConfig(batch_size=8, microbatch_size=9),
        SASRecConfig(microbatch_size=-1),
        SASRecConfig(loss="softmax"),  # type: ignore[arg-type]
        SASRecConfig(device="tpu"),
    ],
)
def test_invalid_wo4_settings_are_rejected(config: SASRecConfig) -> None:
    with pytest.raises(ValueError):
        config.validate()


# --- 3. per-step loss and gradient norm -----------------------------------------


@pytest.mark.parametrize("loss", ["bce", "sampled-softmax", "full-softmax"])
def test_every_step_reports_a_finite_loss_and_gradient_norm(loss: str) -> None:
    steps: list[SASRecTrainingStep] = []
    SASRecModel(config=_small_config(loss=loss, epochs=2), cold_start_threshold=None).fit(
        _small_frame(), retrieval_backend="torch", on_step=steps.append
    )

    assert steps
    assert {step.epoch for step in steps} == {1, 2}
    assert all(math.isfinite(step.loss) and step.loss > 0 for step in steps)
    assert all(math.isfinite(step.grad_norm) and step.grad_norm > 0 for step in steps)


def test_the_gradient_norm_reads_gradients_without_changing_them() -> None:
    torch.manual_seed(0)
    encoder = SASRecEncoder(20, _small_config())
    encoder.encode_positions(torch.tensor([[0, 3, 4, 5]])).pow(2).sum().backward()  # type: ignore[no-untyped-call]
    before = {name: p.grad.clone() for name, p in encoder.named_parameters() if p.grad is not None}

    norm = sasrec._gradient_norm(encoder)

    expected = torch.linalg.vector_norm(torch.cat([grad.flatten() for grad in before.values()]))
    assert norm == pytest.approx(float(expected), rel=1e-6)
    for name, parameter in encoder.named_parameters():
        if parameter.grad is not None:
            assert torch.equal(parameter.grad, before[name])


class _FakeClient:
    def __init__(self) -> None:
        self.batches: list[int] = []
        self.metrics: list[tuple[str, float, int]] = []

    def log_batch(self, run_id: str, *, metrics: list[Any]) -> None:
        self.batches.append(len(metrics))
        self.metrics.extend((metric.key, metric.value, metric.step) for metric in metrics)


def test_step_metrics_travel_in_batches_mlflow_accepts() -> None:
    client = _FakeClient()
    log = StepMetricsLogger("run", client=client)  # type: ignore[arg-type]
    for step in range(1, 1001):
        log(SASRecTrainingStep(epoch=1, step=step, loss=1.0 / step, grad_norm=2.0, examples=8))
    log.flush()

    assert all(size <= 1000 for size in client.batches)
    assert len(client.metrics) == 2000
    losses = [(value, step) for key, value, step in client.metrics if key == STEP_LOSS_METRIC]
    assert losses == [(1.0 / step, step) for step in range(1, 1001)]
    assert sum(key == STEP_GRAD_NORM_METRIC for key, _value, _step in client.metrics) == 1000


def test_the_loss_curve_renders_a_png(tmp_path: Path) -> None:
    log = StepMetricsLogger("run", client=_FakeClient())  # type: ignore[arg-type]
    for step in range(1, 301):
        log(
            SASRecTrainingStep(
                epoch=1 + step // 120, step=step, loss=2.0 / step**0.3, grad_norm=1.0, examples=8
            )
        )
    path = render_loss_curve(log, tmp_path / "curve.png", title="test")

    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


# --- 4. device -------------------------------------------------------------------


def test_cpu_is_the_default_and_an_unavailable_device_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert resolve_device("cpu") == torch.device("cpu")
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="mps"):
        resolve_device("mps")
    with pytest.raises(RuntimeError, match="cuda"):
        resolve_device("cuda")
    with pytest.raises(RuntimeError, match="mps"):
        SASRecModel(config=_small_config(device="mps"), cold_start_threshold=None).fit(
            _small_frame()
        )


@pytest.mark.parametrize("loss", ["bce", "sampled-softmax"])
def test_cpu_training_is_bit_reproducible(loss: str) -> None:
    digests = []
    for _ in range(2):
        model = SASRecModel(
            config=_small_config(loss=loss, dropout=0.2, epochs=2), cold_start_threshold=None
        ).fit(_small_frame(), retrieval_backend="torch")
        assert model._encoder is not None
        digests.append(encoder_weights_sha256(model._encoder.state_dict()))
    assert digests[0] == digests[1]


@pytest.mark.skipif(not MPS, reason="needs the Mac's GPU (torch.backends.mps)")
def test_an_mps_fit_trains_on_the_gpu_and_scores_on_the_cpu() -> None:
    seen_during_training: list[str] = []
    seen_while_scoring: list[str] = []
    config = _small_config(device="mps", epochs=2)
    model = SASRecModel(config=config, cold_start_threshold=None)

    def on_step(step: SASRecTrainingStep) -> None:
        assert model._encoder is not None
        seen_during_training.append(next(model._encoder.parameters()).device.type)

    def on_epoch(_epoch: int, _loss: float) -> None:
        assert model._encoder is not None
        seen_while_scoring.append(next(model._encoder.parameters()).device.type)

    model.fit(_small_frame(), on_epoch=on_epoch, retrieval_backend="torch", on_step=on_step)

    assert set(seen_during_training) == {"mps"}
    assert seen_while_scoring == ["cpu", "cpu"]
    assert model._encoder is not None
    assert {parameter.device.type for parameter in model._encoder.parameters()} == {"cpu"}
    assert len(model.recommend(1, 10)) == 10


@pytest.mark.skipif(not MPS, reason="needs the Mac's GPU (torch.backends.mps)")
def test_mps_and_cpu_draw_the_same_negatives_for_the_same_first_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drawn: dict[str, np.ndarray] = {}
    original = sasrec.sample_uniform_negatives

    for device in ("cpu", "mps"):

        def record(*args: Any, _device: str = device, **kwargs: Any) -> np.ndarray:
            result = original(*args, **kwargs)
            drawn.setdefault(_device, result)
            return result

        monkeypatch.setattr(sasrec, "sample_uniform_negatives", record)
        SASRecModel(config=_small_config(device=device), cold_start_threshold=None).fit(
            _small_frame(), retrieval_backend="torch"
        )
    assert np.array_equal(drawn["cpu"], drawn["mps"])


# --- 5. the run --------------------------------------------------------------------


def test_run_logs_steps_batch_numbers_stopping_and_a_loss_curve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ratings = pd.DataFrame(
        [
            (user, 1000 + (user * 7 + step) % 60, 4.0, step * 1000 + user)
            for user in range(1, 31)
            for step in range(12)
        ],
        columns=["userId", "movieId", "rating", "timestamp"],
    )
    config = _small_config(
        batch_size=32,
        microbatch_size=8,
        epochs=3,
        early_stopping=True,
        early_stopping_min_epochs=2,
        early_stopping_probe_fraction=0.1,
    )
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    previous = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        run_once(ratings, config, sample_fraction=0.5, artifact_root=tmp_path / "artifacts")
        run = mlflow.search_runs(search_all_experiments=True, output_format="list")[0]
        client = MlflowClient()
        params = run.data.params
        steps = client.get_metric_history(run.info.run_id, STEP_LOSS_METRIC)
        norms = client.get_metric_history(run.info.run_id, STEP_GRAD_NORM_METRIC)
        probe = client.get_metric_history(
            run.info.run_id, "early_stopping_probe_recall_at_k_candidates"
        )
        artifacts = [item.path for item in client.list_artifacts(run.info.run_id, "training")]
        curve = Path(
            client.download_artifacts(run.info.run_id, "training/loss_curve.png", str(tmp_path))
        )
    finally:
        mlflow.set_tracking_uri(previous)

    assert params["examples_per_step"] == "32"
    assert params["microbatch_examples"] == "8"
    assert params["gradient_accumulation_steps"] == "4"
    assert params["device"] == "cpu"
    assert int(params["optimizer_steps"]) == len(steps) == len(norms) > 0
    assert sorted(metric.step for metric in steps) == list(range(1, len(steps) + 1))
    assert int(params["epochs_completed"]) == len(probe) in {2, 3}
    assert int(params["early_stopping_probe_users"]) == int(
        params["early_stopping_dropped_targets"]
    )
    assert int(params["early_stopping_probe_latest_timestamp"]) < int(params["cutoff_timestamp"])
    assert artifacts == ["training/loss_curve.png"]
    assert curve.read_bytes()[:4] == b"\x89PNG"


def test_run_refuses_early_stopping_when_the_fitted_frame_reaches_the_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ratings = pd.DataFrame(
        [
            (user, 1000 + step, 4.0, step * 1000 + user)
            for user in range(1, 21)
            for step in range(10)
        ],
        columns=["userId", "movieId", "rating", "timestamp"],
    )
    original = sasrec_training.sealed_partition_params

    def reaching(*args: Any, **kwargs: Any) -> dict[str, int]:
        params = original(*args, **kwargs)
        params["latest_fit_timestamp"] = params["holdout_end_timestamp"]
        return params

    monkeypatch.setattr(sasrec_training, "sealed_partition_params", reaching)
    with pytest.raises(sasrec_training.StoppingProbeError, match="cutoff"):
        run_once(ratings, _small_config(early_stopping=True, epochs=3), sample_fraction=0.5)


def test_the_report_refuses_a_probe_outside_the_training_window() -> None:
    report = sasrec.SASRecTrainingReport.for_config(_small_config())
    report.probe_latest_timestamp = 500
    with pytest.raises(sasrec_training.StoppingProbeError, match="outside the training window"):
        sasrec_training.log_training_report(report, cutoff=500, latest_fit_timestamp=499)


# --- 6. the committed cells ------------------------------------------------------


def test_wo4_cell_0b_pilots_hold_the_v1_shape_and_change_only_the_loss_and_stopping() -> None:
    v1_fraction, v1_cells = parse_grid(
        json.loads(Path("docs/experiments/sasrec/wo1-restored-loop-6pct-s42.json").read_text())
    )
    v1 = v1_cells[0][1]
    pilots = {}
    for name in (
        "wo4-cell0b-pilot-6pct.json",
        "wo4-cell0b-pair-cpu-6pct.json",
        "wo4-cell0b-mps-pilot-6pct.json",
    ):
        spec = json.loads((Path("docs/experiments/sasrec") / name).read_text())
        fraction, cells = parse_grid(spec)
        assert fraction == v1_fraction == 0.06
        assert spec["expected_protocol_hash"] == (
            "sha256:faf2828d08a0b0ecf23993fcfaf037134359017e20c7601b53da7e2ebecc22bc"
        )
        assert len(cells) == 1
        pilots[name] = cells[0][1]

    cpu = pilots["wo4-cell0b-pilot-6pct.json"]
    mps = pilots["wo4-cell0b-mps-pilot-6pct.json"]
    assert (cpu.hidden_dim, cpu.num_blocks, cpu.max_sequence_length) == (64, 2, 50)
    assert cpu.loss == "sampled-softmax" and cpu.negative_count == 1024
    assert cpu.early_stopping and cpu.epochs == 5 and cpu.early_stopping_min_epochs == 3
    assert cpu.device == "cpu" and mps.device == "mps"
    # The pair's CPU half is cell 0b exactly; the mps half differs in the device alone.
    assert pilots["wo4-cell0b-pair-cpu-6pct.json"] == cpu
    assert dataclasses.replace(mps, device="cpu") == cpu
    assert dataclasses.replace(
        cpu, loss="bce", negative_count=32, epochs=2, early_stopping=False
    ) == dataclasses.replace(v1, calibration_t=cpu.calibration_t)
