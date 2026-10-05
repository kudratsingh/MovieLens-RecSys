"""Per-step training telemetry for SASRec runs: MLflow metrics and a loss-curve image (WO-4).

Every optimizer step reports its mean loss and its gradient norm. A full-data pass
is about 38,500 steps, so the metrics travel to MLflow in batches rather than one
call per step; nothing is sampled away.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from mlflow.entities import Metric
from mlflow.tracking import MlflowClient

from src.models.candidates.sasrec import SASRecTrainingStep

STEP_LOSS_METRIC = "train_step_loss"
STEP_GRAD_NORM_METRIC = "train_step_grad_norm"
LOSS_CURVE_FILENAME = "loss_curve.png"
LOSS_CURVE_ARTIFACT_PATH = "training"
# MLflow accepts at most 1,000 metrics per batch; two per step.
_STEPS_PER_FLUSH = 450

# The reference palette's light surface, text and first series hue (the dataviz
# skill's palette.md). One hue: each panel carries one measure, drawn twice — the
# raw steps as a pale trace and their moving average as the line to read.
_SURFACE = "#fcfcfb"
_TEXT_PRIMARY = "#0b0b0b"
_TEXT_SECONDARY = "#52514e"
_GRID = "#e4e3df"
_SERIES = "#2a78d6"
_SERIES_TRACE = "#bcd3f1"


@dataclass
class StepMetricsLogger:
    """Collects each optimizer step and ships it to an MLflow run in batches.

    Pass an instance as ``SASRecModel.fit``'s ``on_step``; call ``flush`` once
    the fit returns. The full series stays in memory for the loss curve — a few
    MB even at 5 full-data passes.
    """

    run_id: str
    client: MlflowClient = field(default_factory=MlflowClient)
    steps: list[int] = field(default_factory=list)
    epochs: list[int] = field(default_factory=list)
    losses: list[float] = field(default_factory=list)
    grad_norms: list[float] = field(default_factory=list)
    _pending: list[Metric] = field(default_factory=list)

    def __call__(self, step: SASRecTrainingStep) -> None:
        self.steps.append(step.step)
        self.epochs.append(step.epoch)
        self.losses.append(step.loss)
        self.grad_norms.append(step.grad_norm)
        stamp = int(time.time() * 1000)
        self._pending.append(Metric(STEP_LOSS_METRIC, step.loss, stamp, step.step))
        self._pending.append(Metric(STEP_GRAD_NORM_METRIC, step.grad_norm, stamp, step.step))
        if len(self._pending) >= 2 * _STEPS_PER_FLUSH:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            self.client.log_batch(self.run_id, metrics=self._pending)
            self._pending = []


def _moving_average(values: list[float], window: int) -> list[float]:
    """Trailing mean over at most ``window`` steps, so the line starts at step 1."""
    averaged: list[float] = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= window:
            total -= values[index - window]
        averaged.append(total / min(index + 1, window))
    return averaged


def render_loss_curve(log: StepMetricsLogger, path: Path, *, title: str) -> Path:
    """Draw loss and gradient norm against optimizer step, one panel each.

    Two measures on two scales get two panels on a shared step axis, never one
    panel with two y-axes. Epoch boundaries are hairlines labeled at the top.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    window = max(1, min(500, len(log.steps) // 50))
    figure, axes = plt.subplots(
        2, 1, figsize=(10, 6.4), sharex=True, dpi=150, constrained_layout=True
    )
    figure.patch.set_facecolor(_SURFACE)
    figure.suptitle(title, color=_TEXT_PRIMARY, fontsize=12, x=0.01, ha="left")
    panels = (
        (axes[0], log.losses, "Training loss (mean over the step's examples)"),
        (axes[1], log.grad_norms, "Gradient norm (global L2, before the update)"),
    )
    boundaries = [
        log.steps[index]
        for index in range(1, len(log.steps))
        if log.epochs[index] != log.epochs[index - 1]
    ]
    for axis, values, label in panels:
        axis.set_facecolor(_SURFACE)
        if log.steps:
            axis.plot(log.steps, values, color=_SERIES_TRACE, linewidth=0.8, label="per step")
            smoothed = _moving_average(values, window)
            axis.plot(
                log.steps,
                smoothed,
                color=_SERIES,
                linewidth=2.0,
                solid_capstyle="round",
                solid_joinstyle="round",
                label=f"moving average, {window} steps",
            )
            axis.annotate(
                f"{smoothed[-1]:.4g}",
                xy=(log.steps[-1], smoothed[-1]),
                xytext=(6, 0),
                textcoords="offset points",
                va="center",
                color=_TEXT_PRIMARY,
                fontsize=9,
            )
        for boundary in boundaries:
            axis.axvline(boundary - 0.5, color=_GRID, linewidth=1.0, zorder=0)
        axis.set_title(label, color=_TEXT_SECONDARY, fontsize=10, loc="left")
        axis.grid(axis="y", color=_GRID, linewidth=0.8)
        axis.tick_params(colors=_TEXT_SECONDARY, labelsize=9)
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            axis.spines[spine].set_color(_GRID)
    for epoch, boundary in enumerate([log.steps[0], *boundaries] if log.steps else [], start=1):
        axes[0].annotate(
            f"epoch {epoch}",
            xy=(boundary, 1.0),
            xycoords=("data", "axes fraction"),
            xytext=(3, -2),
            textcoords="offset points",
            va="top",
            color=_TEXT_SECONDARY,
            fontsize=8,
        )
    axes[1].set_xlabel("optimizer step", color=_TEXT_SECONDARY, fontsize=9)
    if log.steps:
        # Both panels use the same two marks, so one legend serves both. It sits
        # at the right end of the first panel's title row: clear of the figure
        # title however long the run name, and of the epoch labels inside.
        axes[0].legend(
            loc="lower right",
            bbox_to_anchor=(1.0, 1.0),
            borderaxespad=0.0,
            ncols=2,
            frameon=False,
            fontsize=9,
            labelcolor=_TEXT_SECONDARY,
        )
    figure.savefig(path, facecolor=_SURFACE)
    plt.close(figure)
    return path
