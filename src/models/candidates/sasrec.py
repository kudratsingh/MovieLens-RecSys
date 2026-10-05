"""Causal sequential candidate retrieval (SASRec / gSASRec, ADR 0016)."""

from __future__ import annotations

import copy
import math
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F  # noqa: N812
from torch import nn

from src.evaluation.metrics import recall_at_k
from src.evaluation.protocol import K_CANDIDATES

from . import routing
from .popularity import PopularityModel
from .sasrec_early_stopping import (
    DEFAULT_MIN_EPOCHS,
    DEFAULT_MIN_RELATIVE_IMPROVEMENT,
    DEFAULT_PROBE_FRACTION,
    DEFAULT_PROBE_SEED,
    StoppingProbe,
    carve_stopping_probe,
    should_stop,
)
from .sasrec_objectives import (
    BCE_LOSSES,
    FULL_SOFTMAX_LOSS,
    LOSSES,
    SAMPLED_SOFTMAX_LOSS,
    SOFTMAX_LOSSES,
    causal_prefixes,
    forbidden_ids,
    full_softmax_loss,
    sample_uniform_negatives,
    sampled_softmax_loss,
)
from .sequence_data import (
    AllPositionTrainingData,
    SequenceExampleStats,
    StrictPrefixExamples,
    build_all_position_training_data,
    build_strict_prefix_example_store,
    build_user_history,
)
from .transformer import LayerNormalization, TransformerStack

# The objective of record: one example per target, scored at the final
# position of its strict prefix. Run 528b1451 / model a11af5ed (warm
# recall@500 0.5092) was trained on it, and ADR 0020's 2026-09-15 decision
# keeps it the baseline. All-positions is a named ablation (PR #183).
LEGACY_TRAINING_OBJECTIVE = "strict-prefix-final-position-v1"
ALL_POSITION_TRAINING_OBJECTIVE = "all-positions-strict-timestamp-v1"
TRAINING_OBJECTIVES = (LEGACY_TRAINING_OBJECTIVE, ALL_POSITION_TRAINING_OBJECTIVE)

# ``cpu`` is the default and the only bit-reproducible device. ``mps`` (the Mac's
# GPU) and ``cuda`` come after it under ADR 0020's D6 hardware order; a model
# trained on either is moved back to the CPU before it scores anything.
DEVICES = ("cpu", "mps", "cuda")

# Fields added after configuration ids were first minted: WO-1's objective and
# WO-4's trainer settings. Each default reproduces the trainer as it was before the
# field existed, and ``src.training.sasrec._configuration_id`` leaves a field out of
# the id while it holds that default, so no run recorded earlier changes identity.
POST_RECORD_FIELDS = (
    "training_objective",
    "microbatch_size",
    "device",
    "early_stopping",
    "early_stopping_min_epochs",
    "early_stopping_min_relative_improvement",
    "early_stopping_probe_fraction",
    "early_stopping_probe_seed",
)

# Probe users are encoded and scored this many at a time. Fixed, because float
# results depend on the batch shape and the stopping decision should not.
_PROBE_BATCH = 256


@dataclass(frozen=True)
class SASRecConfig:
    max_sequence_length: int = 50
    hidden_dim: int = 64
    num_blocks: int = 2
    num_heads: int = 2
    feedforward_dim: int = 256
    dropout: float = 0.2
    negative_count: int = 64
    loss: Literal["gbce", "bce", "sampled-softmax", "full-softmax"] = "gbce"
    calibration_t: float = 0.5
    batch_size: int = 512
    epochs: int = 3
    learning_rate: float = 1e-3
    faiss_nlist: int = 100
    faiss_nprobe: int = 10
    faiss_exact: bool = False
    seed: int = 42
    training_objective: str = LEGACY_TRAINING_OBJECTIVE
    # WO-4 (ADR 0020). ``batch_size`` is examples per optimizer step — targets per
    # step under the all-positions objective. A positive ``microbatch_size`` runs
    # each step as several forward/backward passes of at most that many examples
    # and accumulates their gradients, for a step memory cannot hold at once; 0
    # means one pass per step.
    microbatch_size: int = 0
    device: str = "cpu"
    # Early stopping on a probe carved from train (``sasrec_early_stopping``).
    # Off, ``epochs`` is the number of passes; on, it is the most passes allowed.
    early_stopping: bool = False
    early_stopping_min_epochs: int = DEFAULT_MIN_EPOCHS
    early_stopping_min_relative_improvement: float = DEFAULT_MIN_RELATIVE_IMPROVEMENT
    early_stopping_probe_fraction: float = DEFAULT_PROBE_FRACTION
    early_stopping_probe_seed: int = DEFAULT_PROBE_SEED

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SASRecConfig:
        source = os.environ if env is None else env
        defaults = cls()

        def value(name: str, cast: Callable[[str], Any], default: Any) -> Any:
            raw = source.get(f"SASREC_{name}", "").strip()
            return default if not raw else cast(raw)

        def boolean(raw: str) -> bool:
            if raw.lower() not in {"true", "false", "1", "0"}:
                raise ValueError(f"invalid boolean: {raw}")
            return raw.lower() in {"true", "1"}

        return cls(
            max_sequence_length=value("MAX_SEQUENCE_LENGTH", int, defaults.max_sequence_length),
            hidden_dim=value("HIDDEN_DIM", int, defaults.hidden_dim),
            num_blocks=value("NUM_BLOCKS", int, defaults.num_blocks),
            num_heads=value("NUM_HEADS", int, defaults.num_heads),
            feedforward_dim=value("FEEDFORWARD_DIM", int, defaults.feedforward_dim),
            dropout=value("DROPOUT", float, defaults.dropout),
            negative_count=value("NEGATIVE_COUNT", int, defaults.negative_count),
            loss=value("LOSS", str, defaults.loss),
            calibration_t=value("CALIBRATION_T", float, defaults.calibration_t),
            batch_size=value("BATCH_SIZE", int, defaults.batch_size),
            epochs=value("EPOCHS", int, defaults.epochs),
            learning_rate=value("LEARNING_RATE", float, defaults.learning_rate),
            faiss_nlist=value("FAISS_NLIST", int, defaults.faiss_nlist),
            faiss_nprobe=value("FAISS_NPROBE", int, defaults.faiss_nprobe),
            faiss_exact=value("FAISS_EXACT", boolean, defaults.faiss_exact),
            seed=value("SEED", int, defaults.seed),
            training_objective=value("TRAINING_OBJECTIVE", str, defaults.training_objective),
            microbatch_size=value("MICROBATCH_SIZE", int, defaults.microbatch_size),
            device=value("DEVICE", str, defaults.device),
            early_stopping=value("EARLY_STOPPING", boolean, defaults.early_stopping),
            early_stopping_min_epochs=value(
                "EARLY_STOPPING_MIN_EPOCHS", int, defaults.early_stopping_min_epochs
            ),
            early_stopping_min_relative_improvement=value(
                "EARLY_STOPPING_MIN_RELATIVE_IMPROVEMENT",
                float,
                defaults.early_stopping_min_relative_improvement,
            ),
            early_stopping_probe_fraction=value(
                "EARLY_STOPPING_PROBE_FRACTION", float, defaults.early_stopping_probe_fraction
            ),
            early_stopping_probe_seed=value(
                "EARLY_STOPPING_PROBE_SEED", int, defaults.early_stopping_probe_seed
            ),
        )

    def as_params(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def microbatch_examples(self) -> int:
        """Examples in one forward/backward pass (``batch_size`` when not split)."""
        return self.microbatch_size or self.batch_size

    @property
    def gradient_accumulation_steps(self) -> int:
        """Forward/backward passes accumulated into one optimizer step, at most."""
        return math.ceil(self.batch_size / self.microbatch_examples)

    def validate(self) -> None:
        if self.max_sequence_length <= 0 or self.hidden_dim <= 0:
            raise ValueError("sequence length and hidden dimension must be positive")
        if self.num_blocks <= 0 or self.num_heads <= 0:
            raise ValueError("num_blocks and num_heads must be positive")
        if self.hidden_dim % self.num_heads:
            raise ValueError("hidden_dim must be divisible by num_heads")
        if self.negative_count <= 0:
            raise ValueError("negative_count must be positive")
        if not 0.0 <= self.calibration_t <= 1.0:
            raise ValueError("calibration_t must be in [0, 1]")
        if self.training_objective not in TRAINING_OBJECTIVES:
            raise ValueError(
                f"unsupported training_objective {self.training_objective!r}; "
                f"expected one of {list(TRAINING_OBJECTIVES)}"
            )
        if self.loss not in LOSSES:
            raise ValueError(f"unsupported loss {self.loss!r}; expected one of {list(LOSSES)}")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if not 0 <= self.microbatch_size <= self.batch_size:
            raise ValueError("microbatch_size must be 0 (one pass per step) or 1..batch_size")
        if self.device not in DEVICES:
            raise ValueError(f"unsupported device {self.device!r}; expected one of {list(DEVICES)}")
        if self.early_stopping:
            if not 1 <= self.early_stopping_min_epochs <= self.epochs:
                raise ValueError("early_stopping_min_epochs must be between 1 and epochs")
            if not 0.0 < self.early_stopping_probe_fraction < 1.0:
                raise ValueError("early_stopping_probe_fraction must be in (0, 1)")
            if self.early_stopping_min_relative_improvement < 0.0:
                raise ValueError("early_stopping_min_relative_improvement must be non-negative")


def resolve_device(name: str) -> torch.device:
    """The torch device a fit runs on. Refuses a device this machine does not have."""
    if name not in DEVICES:
        raise ValueError(f"unsupported device {name!r}; expected one of {list(DEVICES)}")
    if name == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("device 'mps' requested, but torch.backends.mps.is_available() is False")
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("device 'cuda' requested, but torch.cuda.is_available() is False")
    return torch.device(name)


@dataclass(frozen=True)
class SASRecTrainingStep:
    """One optimizer step, as reported to ``fit``'s ``on_step`` callback."""

    epoch: int
    # Optimizer steps taken so far, counted across epochs from 1.
    step: int
    # Mean loss over the step's examples (targets, under all-positions).
    loss: float
    # Global L2 norm over every parameter's gradient, after backward and before
    # the update. Nothing clips it; it is measured, not used.
    grad_norm: float
    examples: int


@dataclass
class SASRecTrainingReport:
    """What one ``fit`` did, beyond its weights: the numbers a run record needs."""

    device: str
    examples_per_step: int
    microbatch_examples: int
    gradient_accumulation_steps: int
    max_epochs: int
    early_stopping: bool
    epochs_completed: int = 0
    optimizer_steps: int = 0
    stopped_early: bool = False
    epoch_losses: list[float] = field(default_factory=list)
    epoch_train_seconds: list[float] = field(default_factory=list)
    probe_recalls: list[float] = field(default_factory=list)
    probe_users: int = 0
    probe_dropped_targets: int = 0
    probe_latest_timestamp: int | None = None

    @classmethod
    def for_config(cls, config: SASRecConfig) -> SASRecTrainingReport:
        return cls(
            device=config.device,
            examples_per_step=config.batch_size,
            microbatch_examples=config.microbatch_examples,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            max_epochs=config.epochs,
            early_stopping=config.early_stopping,
        )


class SASRecEncoder(nn.Module):
    """Pre-normalized causal Transformer with tied input/output items.

    The Transformer itself is the hand-written stack in ``transformer.py`` (WO-2,
    rule D7). Its parameter layout differs from the one SASRec v1 was saved in;
    ``sasrec_artifact`` converts old archives on load, so every saved model keeps
    loading and scoring as it did.
    """

    def __init__(self, n_item_rows: int, config: SASRecConfig) -> None:
        super().__init__()
        self.config = config
        self.item_embedding = nn.Embedding(n_item_rows, config.hidden_dim, padding_idx=0)
        self.position_embedding = nn.Embedding(config.max_sequence_length, config.hidden_dim)
        self.transformer = TransformerStack(
            dim=config.hidden_dim,
            heads=config.num_heads,
            hidden=config.feedforward_dim,
            num_blocks=config.num_blocks,
            dropout=config.dropout,
        )
        self.output_norm = LayerNormalization(config.hidden_dim)
        nn.init.normal_(self.item_embedding.weight, std=1.0 / math.sqrt(config.hidden_dim))
        with torch.no_grad():
            self.item_embedding.weight[0].zero_()

    def encode_positions(self, sequences: torch.Tensor) -> torch.Tensor:
        """``(batch, length)`` left-padded dense ids -> ``(batch, length, hidden_dim)``.

        Padded positions come out as exact zeros. No process-wide backend switch
        is involved: the stack never produces NaN for a padded row, in training or
        inference, so there is no fast path to keep turned off.
        """
        length = sequences.shape[1]
        padding = sequences.eq(0)
        positions = torch.arange(length, device=sequences.device).unsqueeze(0)
        values = self.item_embedding(sequences) + self.position_embedding(positions)
        encoded = self.transformer(values, padding)
        normalized: torch.Tensor = self.output_norm(encoded)
        normalized = normalized.masked_fill(padding.unsqueeze(-1), 0.0)
        return normalized

    def forward(self, sequences: torch.Tensor) -> torch.Tensor:
        """Return the representation at the final (left-padded) position."""
        return F.normalize(self.encode_positions(sequences)[:, -1, :], p=2, dim=-1)

    def training_user_vectors(self, sequences: torch.Tensor) -> torch.Tensor:
        """Unconstrained representations used by the BCE-family objective."""
        return self.encode_positions(sequences)[:, -1, :]

    def item_vectors(self, item_ids: torch.Tensor, *, normalize: bool = True) -> torch.Tensor:
        vectors = self.item_embedding(item_ids)
        return F.normalize(vectors, p=2, dim=-1) if normalize else vectors


def gbce_beta(*, negative_count: int, catalog_size: int, calibration_t: float) -> float:
    """Sampling-rate-independent gBCE beta from Petrov & Macdonald Eq. 27."""
    if catalog_size <= 1:
        return 1.0
    alpha = min(1.0, negative_count / (catalog_size - 1))
    return 1.0 - calibration_t * (1.0 - alpha)


def sampled_gbce_loss(
    positive_logits: torch.Tensor,
    negative_logits: torch.Tensor,
    *,
    beta: float,
) -> torch.Tensor:
    """Numerically stable Eq. 8; beta=1 is ordinary sampled BCE."""
    positive = beta * F.softplus(-positive_logits)
    negative = F.softplus(negative_logits).sum(dim=1)
    return ((positive + negative) / (negative_logits.shape[1] + 1)).mean()


def sample_negatives(
    histories: torch.Tensor,
    positives: torch.Tensor,
    *,
    n_items: int,
    count: int,
    rng: np.random.Generator,
) -> torch.Tensor:
    """Uniform seeded negatives excluding padding, prefix, target, and duplicates."""
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


def sample_position_negatives(
    sequences: torch.Tensor,
    prediction_windows: torch.Tensor,
    prediction_positions: torch.Tensor,
    positives: torch.Tensor,
    *,
    n_items: int,
    count: int,
    rng: np.random.Generator,
) -> torch.Tensor:
    """Sample target negatives without materializing copied prefix rows."""
    output = np.empty((len(positives), count), dtype=np.int64)
    sequence_values = sequences.numpy()
    for row, (window_value, position_value, positive_value) in enumerate(
        zip(prediction_windows.numpy(), prediction_positions.numpy(), positives.numpy())
    ):
        window = int(window_value)
        position = int(position_value)
        history = sequence_values[window, : position + 1]
        forbidden = set(int(item) for item in history if item)
        forbidden.add(int(positive_value))
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


def _window_batches(
    data: AllPositionTrainingData,
    permutation: torch.Tensor,
    *,
    max_predictions: int,
) -> Iterator[torch.Tensor]:
    """Pack shuffled windows while bounding sampled-logit memory by targets."""
    current: list[int] = []
    current_predictions = 0
    for window_value in permutation.tolist():
        window = int(window_value)
        predictions = int(data.prediction_offsets[window + 1] - data.prediction_offsets[window])
        if current and current_predictions + predictions > max_predictions:
            yield torch.tensor(current, dtype=torch.long)
            current = []
            current_predictions = 0
        current.append(window)
        current_predictions += predictions
        if current_predictions >= max_predictions:
            yield torch.tensor(current, dtype=torch.long)
            current = []
            current_predictions = 0
    if current:
        yield torch.tensor(current, dtype=torch.long)


@dataclass(frozen=True)
class _TrainingLoop:
    """Everything a training step needs besides the batch, fixed for one ``fit``."""

    optimizer: torch.optim.Optimizer
    rng: np.random.Generator
    beta: float
    n_items: int
    device: torch.device
    on_epoch: Callable[[int, float], None] | None
    on_step: Callable[[SASRecTrainingStep], None] | None
    probe: StoppingProbe | None


def _microbatch_slices(total: int, limit: int) -> list[slice]:
    """Split ``total`` examples into consecutive passes of at most ``limit`` (0: one pass)."""
    if limit <= 0 or total <= limit:
        return [slice(0, total)]
    return [slice(start, min(total, start + limit)) for start in range(0, total, limit)]


def _window_groups(
    prediction_windows: torch.Tensor, n_windows: int, limit: int
) -> list[tuple[slice, slice]]:
    """Group consecutive windows into passes of at most ``limit`` targets.

    ``AllPositionTrainingData.batch`` lists targets window by window, so each
    group is a slice of windows and the matching slice of targets. A window is
    never split — its targets share one encoder pass — so one window holding more
    than ``limit`` targets is a pass of its own.
    """
    total = len(prediction_windows)
    if limit <= 0 or total <= limit:
        return [(slice(0, n_windows), slice(0, total))]
    if total and bool((prediction_windows[1:] < prediction_windows[:-1]).any()):
        raise ValueError("targets must be listed window by window")
    counts = torch.bincount(prediction_windows, minlength=n_windows).tolist()
    groups: list[tuple[slice, slice]] = []
    window_start = 0
    target_start = 0
    in_group = 0
    for window, count in enumerate(counts):
        if in_group and in_group + int(count) > limit:
            groups.append(
                (slice(window_start, window), slice(target_start, target_start + in_group))
            )
            window_start = window
            target_start += in_group
            in_group = 0
        in_group += int(count)
    groups.append((slice(window_start, n_windows), slice(target_start, target_start + in_group)))
    return groups


def _on(tensor: torch.Tensor, part: slice, whole: bool, device: torch.device) -> torch.Tensor:
    """``tensor[part]`` on ``device``; the tensor itself when the step is one pass."""
    return (tensor if whole else tensor[part]).to(device)


def _backward(loss: torch.Tensor, part: slice, total: int, whole: bool) -> float:
    """Backpropagate one pass's mean loss, weighted by its share of the step.

    Accumulated over the passes, the gradient is the gradient of the step's mean
    loss — equal to one big pass up to float summation order, not bit for bit. A
    one-pass step backpropagates the loss untouched, exactly as v1 did.
    """
    if whole:
        loss.backward()  # type: ignore[no-untyped-call]
        return float(loss.item())
    share = float(int(part.stop) - int(part.start)) / total
    weighted: torch.Tensor = loss * share
    weighted.backward()  # type: ignore[no-untyped-call]
    return float(loss.item()) * share


def _gradient_norm(module: nn.Module) -> float:
    """Global L2 norm of every parameter gradient. Reads the gradients, never changes them."""
    norms = [
        torch.linalg.vector_norm(parameter.grad.detach())
        for parameter in module.parameters()
        if parameter.grad is not None
    ]
    if not norms:
        return 0.0
    return float(torch.linalg.vector_norm(torch.stack(norms)).item())


@dataclass
class SASRecModel:
    config: SASRecConfig = field(default_factory=SASRecConfig)
    cold_start_threshold: int | None = routing.DEFAULT_COLD_START_THRESHOLD
    _encoder: SASRecEncoder | None = None
    _item_to_index: dict[int, int] = field(default_factory=dict)
    _index_to_item: dict[int, int] = field(default_factory=dict)
    _user_history: dict[int, list[int]] = field(default_factory=dict)
    _unknown_index: int = 0
    _training_stats: SequenceExampleStats | None = None
    _training_example_bytes: int = 0
    _training_objective: str = LEGACY_TRAINING_OBJECTIVE
    _training_report: SASRecTrainingReport | None = None
    _faiss_index: Any = None
    _exact_item_matrix: torch.Tensor | None = None
    _popularity: PopularityModel = field(default_factory=PopularityModel)

    def fit(
        self,
        train: pd.DataFrame,
        on_epoch: Callable[[int, float], None] | None = None,
        *,
        retrieval_backend: Literal["faiss", "torch"] = "faiss",
        on_step: Callable[[SASRecTrainingStep], None] | None = None,
    ) -> SASRecModel:
        """Train on ``train`` alone.

        ``on_epoch(epoch, mean_loss)`` runs after each pass and ``on_step`` after
        each optimizer step; both are for logging. Neither can change what is
        trained or when training stops: with early stopping on, the decision is
        taken from the probe carved out of ``train`` before ``on_epoch`` runs, and
        nothing either callback returns is read.
        """
        self.config.validate()
        if retrieval_backend not in {"faiss", "torch"}:
            raise ValueError(f"unsupported retrieval backend: {retrieval_backend}")
        device = resolve_device(self.config.device)
        self._training_objective = self.config.training_objective
        self._training_report = SASRecTrainingReport.for_config(self.config)
        self._popularity = PopularityModel().fit(train)
        if train.empty:
            return self
        # Seeding, vocabulary, example construction, encoder initialization,
        # optimizer and sampler RNG happen in exactly the order the trainer of
        # record used. Example construction draws no randomness, so the encoder
        # starts from the same weights under either objective.
        torch.manual_seed(self.config.seed)
        np.random.seed(self.config.seed)
        items = sorted(int(item) for item in train["movieId"].unique())
        self._item_to_index = {item: index + 1 for index, item in enumerate(items)}
        self._index_to_item = {index: item for item, index in self._item_to_index.items()}
        self._unknown_index = len(items) + 1
        self._user_history = build_user_history(train, self._item_to_index)
        # The probe leaves the training examples only. The vocabulary, every
        # user's history and the popularity fallback still come from all of
        # ``train``: the model knows every movie, it just never trains on the
        # probe targets. The probe draws from its own generator, so the
        # training RNG stream is the same with early stopping on or off.
        example_frame = train
        probe: StoppingProbe | None = None
        if self.config.early_stopping:
            example_frame, probe = carve_stopping_probe(
                train,
                fraction=self.config.early_stopping_probe_fraction,
                seed=self.config.early_stopping_probe_seed,
            )
            self._training_report.probe_users = len(probe)
            self._training_report.probe_dropped_targets = len(train) - len(example_frame)
            self._training_report.probe_latest_timestamp = probe.latest_timestamp
        examples: StrictPrefixExamples | None = None
        training_data: AllPositionTrainingData | None = None
        if self._training_objective == LEGACY_TRAINING_OBJECTIVE:
            examples = build_strict_prefix_example_store(
                example_frame,
                item_to_index=self._item_to_index,
                max_length=self.config.max_sequence_length,
            )
            self._training_stats = examples.stats
            self._training_example_bytes = examples.nbytes
        else:
            training_data = build_all_position_training_data(
                example_frame,
                item_to_index=self._item_to_index,
                max_length=self.config.max_sequence_length,
            )
            self._training_stats = training_data.stats
            self._training_example_bytes = sum(
                tensor.numel() * tensor.element_size()
                for tensor in (
                    training_data.sequences,
                    training_data.prediction_offsets,
                    training_data.prediction_positions,
                    training_data.positives,
                )
            )
        # Built on the CPU so initialization draws from the same generator, in the
        # same order, whatever the device; then moved. On the CPU this is a no-op.
        self._encoder = SASRecEncoder(len(items) + 2, self.config)
        if device.type != "cpu":
            self._encoder.to(device)
        optimizer = torch.optim.Adam(self._encoder.parameters(), lr=self.config.learning_rate)
        rng = np.random.default_rng(self.config.seed)
        beta = (
            gbce_beta(
                negative_count=self.config.negative_count,
                catalog_size=len(items),
                calibration_t=self.config.calibration_t,
            )
            if self.config.loss == "gbce"
            else 1.0
        )
        loop = _TrainingLoop(
            optimizer=optimizer,
            rng=rng,
            beta=beta,
            n_items=len(items),
            device=device,
            on_epoch=on_epoch,
            on_step=on_step,
            probe=probe,
        )
        if examples is not None:
            self._train_strict_prefix(examples, loop)
        else:
            assert training_data is not None
            self._train_all_positions(training_data, loop)
        if device.type != "cpu":
            # Everything this model scores from here on — the index, the final
            # evaluation, the export — is computed on the CPU from the trained
            # weights, so every published number comes from one machine (D6).
            self._encoder.to("cpu")
        if retrieval_backend == "faiss":
            self.build_index()
        else:
            self.build_exact_tensor_index()
        return self

    @property
    def training_report(self) -> SASRecTrainingReport | None:
        return self._training_report

    def _train_strict_prefix(self, examples: StrictPrefixExamples, loop: _TrainingLoop) -> None:
        """The trainer of record's loop (``git show 89520be^``), on the P1 data path.

        For the BCE family it is, statement for statement, the loop behind run
        528b1451, with one change: ``examples.batch(rows)`` cuts each window from
        the stored sequences where the original indexed a pre-built
        ``(n_examples, max_length)`` tensor. The permutation, the batches, the
        sampler's draws, and the per-batch-mean epoch loss are untouched, so a
        fixed seed on CPU gives the same weights bit for bit. The per-example
        negative sampler stays as it was for this family so v1 numbers remain
        reproducible (pinned by ``tests/unit/test_sasrec_trainer_upgrades.py``).

        WO-4 adds, without changing that path: the softmax losses and their
        vectorized sampler, gradient accumulation, a device, per-step reporting,
        and early stopping.
        """
        assert self._encoder is not None
        for epoch in range(self.config.epochs):
            self._encoder.train()
            started = time.perf_counter()
            permutation = torch.randperm(len(examples))
            epoch_loss = 0.0
            batches = 0
            for start in range(0, len(examples), self.config.batch_size):
                rows = permutation[start : start + self.config.batch_size]
                history_batch, positive_batch = examples.batch(rows)
                step_loss = self._strict_prefix_step(history_batch, positive_batch, loop, epoch + 1)
                epoch_loss += step_loss
                batches += 1
            mean_loss = epoch_loss / max(1, batches)
            if self._end_epoch(epoch + 1, mean_loss, time.perf_counter() - started, loop):
                break

    def _strict_prefix_step(
        self,
        histories: torch.Tensor,
        positives: torch.Tensor,
        loop: _TrainingLoop,
        epoch: int,
    ) -> float:
        """One optimizer step over one batch of examples; returns its mean loss."""
        assert self._encoder is not None
        negatives = self._draw_negatives(histories, positives, loop)
        chunks = _microbatch_slices(len(positives), self.config.microbatch_size)
        loop.optimizer.zero_grad()
        step_loss = 0.0
        for chunk in chunks:
            whole = len(chunks) == 1
            loss = self._strict_prefix_loss(
                _on(histories, chunk, whole, loop.device),
                _on(positives, chunk, whole, loop.device),
                None if negatives is None else _on(negatives, chunk, whole, loop.device),
                loop,
            )
            step_loss += _backward(loss, chunk, len(positives), whole)
        self._finish_step(loop, epoch, step_loss, len(positives))
        return step_loss

    def _strict_prefix_loss(
        self,
        histories: torch.Tensor,
        positives: torch.Tensor,
        negatives: torch.Tensor | None,
        loop: _TrainingLoop,
    ) -> torch.Tensor:
        assert self._encoder is not None
        user_vectors = self._encoder.training_user_vectors(histories)
        return self._objective_loss(user_vectors, positives, negatives, lambda: histories, loop)

    def _objective_loss(
        self,
        user_vectors: torch.Tensor,
        positives: torch.Tensor,
        negatives: torch.Tensor | None,
        history: Callable[[], torch.Tensor],
        loop: _TrainingLoop,
    ) -> torch.Tensor:
        """The configured loss for ``(rows, d)`` training vectors and their targets.

        ``history`` builds each row's history only when the full softmax needs it
        as a mask. The BCE family is v1's statements, untouched.

        The sampled softmax computes its logits as one product with the whole
        item table and then picks the target's and the negatives' columns. That
        is the same arithmetic as gathering 1,024 item vectors per row, but a
        dense matrix product and its gradient are far cheaper on a CPU than
        gathering half a million rows and scattering their gradients back: at
        cell 0b's shape it costs about 4% over the encoder instead of about 30%.
        """
        assert self._encoder is not None
        if self.config.loss in SOFTMAX_LOSSES:
            items = self._encoder.item_embedding.weight[1 : loop.n_items + 1]
            if self.config.loss == FULL_SOFTMAX_LOSS:
                return full_softmax_loss(user_vectors, items, positives, history())
            assert negatives is not None
            logits = user_vectors @ items.T
            return sampled_softmax_loss(
                logits.gather(1, (positives - 1).unsqueeze(1)).squeeze(1),
                logits.gather(1, negatives - 1),
            )
        assert negatives is not None
        positive_logits = (
            user_vectors * self._encoder.item_vectors(positives, normalize=False)
        ).sum(dim=1)
        negative_logits = torch.einsum(
            "bd,bkd->bk",
            user_vectors,
            self._encoder.item_vectors(negatives, normalize=False),
        )
        return sampled_gbce_loss(positive_logits, negative_logits, beta=loop.beta)

    def _draw_negatives(
        self, histories: torch.Tensor, positives: torch.Tensor, loop: _TrainingLoop
    ) -> torch.Tensor | None:
        """The step's negatives, drawn once before any microbatch so the stream
        of draws does not depend on how the step is split."""
        if self.config.loss in BCE_LOSSES:
            # v1's per-example sampler, kept for the BCE family alone so every v1
            # number stays reproducible bit for bit.
            return sample_negatives(
                histories,
                positives,
                n_items=loop.n_items,
                count=self.config.negative_count,
                rng=loop.rng,
            )
        if self.config.loss == SAMPLED_SOFTMAX_LOSS:
            return torch.from_numpy(
                sample_uniform_negatives(
                    forbidden_ids(histories, positives),
                    n_items=loop.n_items,
                    count=self.config.negative_count,
                    rng=loop.rng,
                )
            )
        return None

    def _train_all_positions(
        self, training_data: AllPositionTrainingData, loop: _TrainingLoop
    ) -> None:
        """One causal pass per bounded window, every eligible target supervised (PR #183)."""
        assert self._encoder is not None
        for epoch in range(self.config.epochs):
            self._encoder.train()
            started = time.perf_counter()
            permutation = torch.randperm(len(training_data.sequences))
            epoch_loss = 0.0
            predictions_seen = 0
            for rows in _window_batches(
                training_data,
                permutation,
                max_predictions=self.config.batch_size,
            ):
                step_loss, batch_predictions = self._all_positions_step(
                    training_data, rows, loop, epoch + 1
                )
                epoch_loss += step_loss * batch_predictions
                predictions_seen += batch_predictions
            mean_loss = epoch_loss / max(1, predictions_seen)
            if self._end_epoch(epoch + 1, mean_loss, time.perf_counter() - started, loop):
                break

    def _all_positions_step(
        self,
        training_data: AllPositionTrainingData,
        rows: torch.Tensor,
        loop: _TrainingLoop,
        epoch: int,
    ) -> tuple[float, int]:
        """One optimizer step over a batch of windows; returns (mean loss, targets)."""
        assert self._encoder is not None
        sequences, prediction_windows, prediction_positions, positive_batch = training_data.batch(
            rows
        )
        negatives: torch.Tensor | None = None
        if self.config.loss in BCE_LOSSES:
            negatives = sample_position_negatives(
                sequences,
                prediction_windows,
                prediction_positions,
                positive_batch,
                n_items=loop.n_items,
                count=self.config.negative_count,
                rng=loop.rng,
            )
        elif self.config.loss == SAMPLED_SOFTMAX_LOSS:
            prefixes = causal_prefixes(sequences.long(), prediction_windows, prediction_positions)
            negatives = torch.from_numpy(
                sample_uniform_negatives(
                    forbidden_ids(prefixes, positive_batch),
                    n_items=loop.n_items,
                    count=self.config.negative_count,
                    rng=loop.rng,
                )
            )
        groups = _window_groups(prediction_windows, len(rows), self.config.microbatch_size)
        total = len(positive_batch)
        loop.optimizer.zero_grad()
        step_loss = 0.0
        for windows, targets in groups:
            whole = len(groups) == 1
            local_windows = prediction_windows if whole else prediction_windows[targets]
            loss = self._all_positions_loss(
                _on(sequences, windows, whole, loop.device).long(),
                (local_windows - (0 if whole else windows.start)).to(loop.device),
                _on(prediction_positions, targets, whole, loop.device),
                _on(positive_batch, targets, whole, loop.device),
                None if negatives is None else _on(negatives, targets, whole, loop.device),
                loop,
            )
            step_loss += _backward(loss, targets, total, whole)
        self._finish_step(loop, epoch, step_loss, total)
        return step_loss, total

    def _all_positions_loss(
        self,
        sequences: torch.Tensor,
        prediction_windows: torch.Tensor,
        prediction_positions: torch.Tensor,
        positives: torch.Tensor,
        negatives: torch.Tensor | None,
        loop: _TrainingLoop,
    ) -> torch.Tensor:
        assert self._encoder is not None
        encoded_positions = self._encoder.encode_positions(sequences)
        user_vectors = encoded_positions[prediction_windows, prediction_positions]
        return self._objective_loss(
            user_vectors,
            positives,
            negatives,
            lambda: causal_prefixes(sequences, prediction_windows, prediction_positions),
            loop,
        )

    def _finish_step(
        self, loop: _TrainingLoop, epoch: int, step_loss: float, examples: int
    ) -> None:
        """Measure the gradient, update, re-zero the padding row, and report."""
        assert self._encoder is not None and self._training_report is not None
        grad_norm = _gradient_norm(self._encoder)
        loop.optimizer.step()
        with torch.no_grad():
            self._encoder.item_embedding.weight[0].zero_()
        self._training_report.optimizer_steps += 1
        if loop.on_step is not None:
            loop.on_step(
                SASRecTrainingStep(
                    epoch=epoch,
                    step=self._training_report.optimizer_steps,
                    loss=step_loss,
                    grad_norm=grad_norm,
                    examples=examples,
                )
            )

    def _end_epoch(self, epoch: int, mean_loss: float, seconds: float, loop: _TrainingLoop) -> bool:
        """Record the pass, decide whether to stop, then hand the pass to ``on_epoch``.

        The decision comes first and from the probe alone. ``on_epoch`` — which in
        ``run_once`` scores the 28-day holdout for the log — runs after it, and
        whatever it returns is ignored, so the holdout has no way into the decision.
        """
        report = self._training_report
        assert report is not None
        report.epochs_completed = epoch
        report.epoch_losses.append(mean_loss)
        report.epoch_train_seconds.append(seconds)
        stop = False
        with self._scoring_on_cpu(loop.device):
            if loop.probe is not None:
                report.probe_recalls.append(self._probe_recall(loop.probe))
                stop = should_stop(
                    report.probe_recalls,
                    min_epochs=self.config.early_stopping_min_epochs,
                    min_relative_improvement=self.config.early_stopping_min_relative_improvement,
                )
            if loop.on_epoch is not None:
                loop.on_epoch(epoch, mean_loss)
        report.stopped_early = stop and epoch < self.config.epochs
        return stop

    @contextmanager
    def _scoring_on_cpu(self, device: torch.device) -> Iterator[None]:
        """Score a CPU copy of the encoder while training continues on ``device``.

        The live encoder stays where its optimizer state is; the copy draws no
        random numbers and is dropped afterwards. On the CPU there is nothing to
        copy, and the live encoder is scored in place exactly as before WO-4.
        """
        if device.type == "cpu":
            yield
            return
        live = self._encoder
        assert live is not None
        self._encoder = copy.deepcopy(live).to("cpu")
        try:
            yield
        finally:
            self._encoder = live

    def _probe_recall(self, probe: StoppingProbe) -> float:
        """Recall@500 on the probe: does each probe user's held-out movie make the top 500?

        Scored as retrieval scores every warm user — the normalized encoding of
        the latest ``max_sequence_length`` movies against normalized item vectors,
        exact search, the user's whole history excluded — and averaged through
        ``src.evaluation.metrics.recall_at_k``. The inputs are the probe's
        histories and targets, all taken from the training frame.
        """
        assert self._encoder is not None
        self._encoder.eval()
        n_items = len(self._index_to_item)
        length = self.config.max_sequence_length
        recalls: list[float] = []
        with torch.no_grad():
            item_matrix = self._encoder.item_vectors(torch.arange(1, n_items + 1))
            for start in range(0, len(probe), _PROBE_BATCH):
                stop = min(len(probe), start + _PROBE_BATCH)
                histories = [
                    [self._item_to_index[movie] for movie in probe.histories[index]]
                    for index in range(start, stop)
                ]
                batch = torch.zeros((len(histories), length), dtype=torch.long)
                for row, history in enumerate(histories):
                    window = history[-length:]
                    batch[row, length - len(window) :] = torch.tensor(window)
                queries = F.normalize(self._encoder.encode_positions(batch)[:, -1, :], p=2, dim=-1)
                scores = queries @ item_matrix.T
                for row, history in enumerate(histories):
                    scores[row, torch.tensor(history) - 1] = float("-inf")
                top_scores, top = torch.topk(scores, k=min(K_CANDIDATES, n_items), dim=1)
                for row, index in enumerate(range(start, stop)):
                    retrieved = [
                        self._index_to_item[int(dense) + 1]
                        for dense, score in zip(top[row].tolist(), top_scores[row].tolist())
                        if score != float("-inf")
                    ]
                    recalls.append(recall_at_k({probe.targets[index]}, retrieved, K_CANDIDATES))
        return float(np.mean(recalls)) if recalls else 0.0

    def build_index(self) -> None:
        if self._encoder is None:
            return
        # Keep FAISS out of the training process. Artifact loading and serving
        # cross this lazy boundary; the trainer evaluates with torch exact
        # top-k and therefore never loads FAISS's second OpenMP runtime.
        import faiss

        # Retrieval must be deterministic: disable dropout before building the
        # item index and leave the fitted model in inference mode. ``fit``
        # explicitly restores training mode at the start of every epoch.
        self._encoder.eval()
        n_items = len(self._index_to_item)
        with torch.no_grad():
            vectors = self._encoder.item_vectors(torch.arange(1, n_items + 1)).numpy()
        if self.config.faiss_exact:
            index: Any = faiss.IndexFlatIP(self.config.hidden_dim)
        else:
            quantizer = faiss.IndexFlatIP(self.config.hidden_dim)
            nlist = min(self.config.faiss_nlist, max(1, n_items // 4))
            index = faiss.IndexIVFFlat(
                quantizer, self.config.hidden_dim, nlist, faiss.METRIC_INNER_PRODUCT
            )
            index.train(vectors)
            index.nprobe = self.config.faiss_nprobe
        index.add(vectors)
        self._faiss_index = index
        self._exact_item_matrix = None

    def build_exact_tensor_index(self) -> None:
        """Build the exhaustive torch index used by training-time evaluation."""
        if self._encoder is None:
            return
        self._encoder.eval()
        n_items = len(self._index_to_item)
        with torch.no_grad():
            self._exact_item_matrix = self._encoder.item_vectors(
                torch.arange(1, n_items + 1)
            ).detach()
        self._faiss_index = None

    def _has_retrieval_index(self) -> bool:
        return self._faiss_index is not None or self._exact_item_matrix is not None

    def _search_index(
        self, queries: np.ndarray[Any, Any], k: int
    ) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
        if self._faiss_index is not None:
            scores, indices = self._faiss_index.search(queries, k)
            return scores, indices
        if self._exact_item_matrix is None:
            raise RuntimeError("SASRec retrieval index is not loaded")
        with torch.no_grad():
            similarities = torch.from_numpy(queries) @ self._exact_item_matrix.T
            scores, indices = torch.topk(similarities, k=k, dim=1, largest=True, sorted=True)
        return scores.numpy(), indices.numpy()

    def was_served_by_sasrec(self, user_id: int) -> bool:
        history = self._user_history.get(user_id, [])
        if self._encoder is None or not self._has_retrieval_index() or not history:
            return False
        return self.cold_start_threshold is None or len(history) >= self.cold_start_threshold

    def recommend(self, user_id: int, k: int) -> list[int]:
        if not self.was_served_by_sasrec(user_id):
            return self._popularity.recommend(user_id, k)
        assert self._encoder is not None and self._has_retrieval_index()
        full_history = self._user_history[user_id]
        return self._recommend_from_dense_history(full_history, k)

    def encode_movie_history(self, movie_ids: list[int]) -> torch.Tensor:
        """Encode ordered movie ids without depending on fitted user state.

        This is the artifact/serving boundary. Unknown snapshot items receive
        the explicit unknown token and can never alias a trained title.
        """
        if self._encoder is None:
            raise RuntimeError("SASRec encoder is not fitted")
        if not movie_ids:
            raise ValueError("SASRec history must contain at least one movie")
        dense_history = [
            self._item_to_index.get(int(movie_id), self._unknown_index) for movie_id in movie_ids
        ]
        sequence = self._sequence_tensor(dense_history)
        with torch.no_grad():
            encoded: torch.Tensor = self._encoder(sequence)
        return encoded

    def recommend_from_history(
        self,
        movie_ids: list[int],
        k: int,
        *,
        excluded_movie_ids: set[int] | None = None,
    ) -> list[int]:
        """Retrieve from an ordered runtime history using an exported model."""
        if k <= 0:
            return []
        if self._encoder is None or not self._has_retrieval_index():
            raise RuntimeError("SASRec model and retrieval index are not loaded")
        if not movie_ids:
            raise ValueError("SASRec history must contain at least one movie")
        dense_history = [
            self._item_to_index.get(int(movie_id), self._unknown_index) for movie_id in movie_ids
        ]
        dense_exclusions = {
            dense
            for movie_id in (set(movie_ids) | (excluded_movie_ids or set()))
            if (dense := self._item_to_index.get(int(movie_id))) is not None
        }
        return self._recommend_from_dense_history(
            dense_history,
            k,
            dense_exclusions=dense_exclusions,
        )

    def recommend_from_history_scored(
        self,
        movie_ids: list[int],
        k: int,
        *,
        excluded_movie_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        """Return runtime candidates with their FAISS inner-product scores.

        Candidate selection follows the configured index. The score attached to
        each returned item is FAISS's exact inner product between the normalized
        query and that stored item vector; with ``faiss_exact=True`` this is also
        the exhaustive exact-search ordering used by the pinned artifact.
        """
        if k <= 0:
            return []
        if self._encoder is None or not self._has_retrieval_index():
            raise RuntimeError("SASRec model and retrieval index are not loaded")
        if not movie_ids:
            raise ValueError("SASRec history must contain at least one movie")
        dense_history = [
            self._item_to_index.get(int(movie_id), self._unknown_index) for movie_id in movie_ids
        ]
        dense_exclusions = {
            dense
            for movie_id in (set(movie_ids) | (excluded_movie_ids or set()))
            if (dense := self._item_to_index.get(int(movie_id))) is not None
        }
        return self._recommend_scored_from_dense_history(
            dense_history,
            k,
            dense_exclusions=dense_exclusions,
        )

    def encode_histories(self, histories: Sequence[Sequence[int]]) -> tuple[np.ndarray, np.ndarray]:
        """Batch-encode ordered movie histories into both representations.

        Returns ``(normalized, unnormalized)``: the L2-normalised query vectors
        FAISS searches with, and the unconstrained representations the
        BCE-family objective calibrated. Both come out of a *single*
        ``encode_positions`` pass, because ``forward`` is literally
        ``F.normalize(encode_positions(x)[:, -1, :])`` — so the normalised half
        returned here is bit-identical to the query
        ``retrieve_unfiltered`` would have built, and the two ADR 0018 ranker
        features cannot drift from the retrieval they are supposed to describe.

        Batching is part of the contract, not an optimisation detail. Rows do
        not interact — causal attention plus the padding mask keeps each
        sequence independent — but float32 matmul is not associative, so a
        row's values depend on the batch size the caller chose. Callers pin and
        log it.
        """
        if self._encoder is None:
            raise RuntimeError("SASRec encoder is not fitted")
        if any(len(movie_ids) == 0 for movie_ids in histories):
            raise ValueError("SASRec history must contain at least one movie")
        max_length = self.config.max_sequence_length
        batch = torch.zeros((len(histories), max_length), dtype=torch.long)
        for row, movie_ids in enumerate(histories):
            dense = [
                self._item_to_index.get(int(movie_id), self._unknown_index)
                for movie_id in movie_ids[-max_length:]
            ]
            batch[row, max_length - len(dense) :] = torch.tensor(dense, dtype=torch.long)
        return self._encode_batch(batch)

    def encode_dense_history(self, dense_history: list[int]) -> tuple[np.ndarray, np.ndarray]:
        """The batch-of-one counterpart, over an already-dense history.

        ``recommend`` and ``_recommend_from_dense_history`` encode one sequence
        at a time, so the holdout-time ranker features have to as well: matching
        the call *shape* rather than only the call site is what makes the
        feature equal to the score that ordered the candidate, instead of merely
        close to it.
        """
        if self._encoder is None:
            raise RuntimeError("SASRec encoder is not fitted")
        sequence = self._sequence_tensor(dense_history[-self.config.max_sequence_length :])
        return self._encode_batch(sequence)

    def _encode_batch(self, batch: torch.Tensor) -> tuple[np.ndarray, np.ndarray]:
        assert self._encoder is not None
        with torch.no_grad():
            raw = self._encoder.encode_positions(batch)[:, -1, :]
            normalized = F.normalize(raw, p=2, dim=-1)
        return normalized.numpy(), raw.numpy()

    def item_matrices(self) -> tuple[np.ndarray, np.ndarray]:
        """``(normalized, unnormalized)`` item embeddings for dense ids 1..n.

        Row ``d - 1`` holds dense item ``d``, which is the same layout the FAISS
        index was built with (``build_index`` adds ``item_vectors(arange(1, n +
        1))`` in order), so a lookup here and a FAISS hit refer to the same
        vector without a second mapping to get wrong.
        """
        if self._encoder is None:
            raise RuntimeError("SASRec encoder is not fitted")
        item_ids = torch.arange(1, len(self._index_to_item) + 1)
        with torch.no_grad():
            normalized = self._encoder.item_vectors(item_ids).numpy()
            unnormalized = self._encoder.item_vectors(item_ids, normalize=False).numpy()
        return normalized, unnormalized

    def dense_index_for(self, movie_id: int) -> int | None:
        """The encoder's dense id for a movie id, or ``None`` if unknown."""
        return self._item_to_index.get(int(movie_id))

    def retrieve_unfiltered(self, histories: Sequence[Sequence[int]], k: int) -> list[list[int]]:
        """Batch top-k over ordered movie histories with nothing excluded.

        The training-time counterpart of ``ItemItemModel.recommend(...,
        filter_seen=False)``. ``recommend_from_history`` is the serving shape and
        removes the history from its own results, which is right online and wrong
        when assembling LambdaRank groups: the positive is drawn from the user's
        train history, so a retriever that filters its own history would drop
        every positive it was asked about. Exclusions belong to the caller here,
        applied to the *negatives* pool after the positive has been kept
        (`src/training/sasrec_ranker.py`, and #126 for the rule itself).

        Batched because the ranker asks this ~154k times per run and a batch of
        one spends most of its time in PyTorch dispatch rather than arithmetic.
        Rows do not interact — causal attention plus the padding mask keeps each
        sequence independent — so a row's result depends only on its own history
        and on the batch size the caller chose, which callers pin and log.
        """
        if self._encoder is None or not self._has_retrieval_index():
            raise RuntimeError("SASRec model and retrieval index are not loaded")
        if not histories:
            return []
        if k <= 0:
            return [[] for _ in histories]
        # One encode, shared with the ADR 0018 ranker features: the normalised
        # half is the query, and a caller that wants the features asks
        # `encode_histories` for the same pair rather than running the encoder
        # a second time and hoping the two agree.
        queries, _unnormalized = self.encode_histories(histories)
        return self.retrieve_from_queries(queries, k)

    def retrieve_from_queries(self, queries: np.ndarray, k: int) -> list[list[int]]:
        """Search the index with query vectors the caller already encoded.

        Split out of ``retrieve_unfiltered`` so a caller that also needs the
        ADR 0018 ranker features encodes **once** and uses the same vectors for
        both jobs. Anything else would make the feature a re-derivation of the
        score rather than the score itself.
        """
        if not self._has_retrieval_index():
            raise RuntimeError("SASRec retrieval index is not loaded")
        if k <= 0 or len(queries) == 0:
            return [[] for _ in range(len(queries))]
        search_k = min(len(self._index_to_item), k)
        _scores, indices = self._search_index(queries, search_k)
        return [
            [self._index_to_item[int(index) + 1] for index in row if index >= 0] for row in indices
        ]

    def _sequence_tensor(self, dense_history: list[int]) -> torch.Tensor:
        history = dense_history[-self.config.max_sequence_length :]
        sequence = torch.zeros((1, self.config.max_sequence_length), dtype=torch.long)
        if history:
            sequence[0, -len(history) :] = torch.tensor(history)
        return sequence

    def _recommend_from_dense_history(
        self,
        full_history: list[int],
        k: int,
        *,
        dense_exclusions: set[int] | None = None,
    ) -> list[int]:
        return [
            movie_id
            for movie_id, _score in self._recommend_scored_from_dense_history(
                full_history,
                k,
                dense_exclusions=dense_exclusions,
            )
        ]

    def _recommend_scored_from_dense_history(
        self,
        full_history: list[int],
        k: int,
        *,
        dense_exclusions: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        assert self._encoder is not None and self._has_retrieval_index()
        history = full_history[-self.config.max_sequence_length :]
        sequence = self._sequence_tensor(history)
        with torch.no_grad():
            query = self._encoder(sequence).numpy()
        # Exclusions cover the full known history, not only the encoder window.
        seen = set(full_history) | (dense_exclusions or set())
        search_k = min(len(self._index_to_item), k + len(seen))
        scores, indices = self._search_index(query, search_k)
        return [
            (self._index_to_item[int(index) + 1], float(score))
            for score, index in zip(scores[0], indices[0])
            if index >= 0 and int(index) + 1 not in seen
        ][:k]

    def recommend_for_users(self, user_ids: list[int], k: int) -> dict[int, list[int]]:
        return {user_id: self.recommend(user_id, k) for user_id in user_ids}
