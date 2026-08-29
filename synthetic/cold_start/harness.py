"""Trainer-side glue for ADR 0011's cold-start cohort.

Four trainers need the same five steps — load the cohort, attach its history to
the train slice, recommend for its users, read the per-bucket slices back off
the ``EvalResult``, and log them — and none of them should carry a private copy
of the metric names. This module owns the shape; each trainer's diff is a call
to :func:`prepare`, a longer list of user ids to recommend for, two keyword
arguments on its existing ``evaluate`` call, and a logging block.

The metric names are ADR 0011's: ``synth_cold_recall_at_k_candidates_h{0,1,3,10}``
for the candidate stage, ``synth_cold_recall_at_k_h*`` for the ranker
end-to-end, ``synth_cold_fallback_served_h*`` for the routing attribution, and
the ``synth_cold_routing_ok`` tag.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.data.split import TemporalSplit
from src.evaluation.protocol import COLD_START_THRESHOLD, EvalResult, SyntheticColdSlice
from synthetic.cold_start.config import COHORT_PARQUET_PATH
from synthetic.cold_start.load import (
    TRAIN_COLUMNS,
    CohortLoadError,
    SyntheticColdCohort,
    load_cohort_if_present,
)

METRIC_PREFIX = "synth_cold"
ROUTING_TAG = "synth_cold_routing_ok"

# Named for the K a trainer evaluates at rather than for its stage, mirroring
# the repo's existing ``*_at_k_candidates`` / ``*_at_k`` convention: a
# candidate-stage recall@500 and an end-to-end recall@10 must never share a
# metric name, whichever model produced them.
SUFFIX_AT_K_CANDIDATES = "at_k_candidates"
SUFFIX_AT_K = "at_k"


class CohortCutoffMismatchError(CohortLoadError):
    """The cohort is anchored to a different split cutoff than this run.

    The cohort's timestamps sit 24 hours before the cutoff that was current
    when it was generated. Its data version already pins the dataset, and
    ``temporal_split`` is a pure function of that dataset — so if the cutoff has
    moved anyway, the split rule itself changed and the cohort's rows are no
    longer where the ADR says they are.
    """


def prepare(
    split: TemporalSplit,
    *,
    logger: logging.Logger,
    path: Path = COHORT_PARQUET_PATH,
) -> tuple[pd.DataFrame, SyntheticColdCohort | None]:
    """Return the frame to fit on, and the cohort if this machine has one.

    On a checkout without the parquet this is the identity on ``split.train``
    and ``None`` — ``make train-itemitem`` keeps working, it just reports no
    cold-start coverage.
    """
    cohort = load_cohort_if_present(path)
    if cohort is None:
        return split.train, None

    if cohort.provenance.split_cutoff != split.cutoff:
        raise CohortCutoffMismatchError(
            f"{path} is anchored to cutoff {cohort.provenance.split_cutoff} but this run "
            f"split at {split.cutoff}. Regenerate it with `make synth-cold-cohort`."
        )

    train = attach_history(split.train, cohort)
    logger.info(
        "Attached ADR 0011 cold-start cohort: %d users across buckets %s, "
        "%d history rows (%.3f%% of train), fingerprint=%s",
        cohort.n_users,
        list(cohort.buckets),
        len(cohort.history),
        100.0 * len(cohort.history) / max(len(train), 1),
        cohort.provenance.fingerprint[:12],
    )
    return train, cohort


def attach_history(train: pd.DataFrame, cohort: SyntheticColdCohort) -> pd.DataFrame:
    """Concatenate the cohort's history rows onto a train slice.

    Only history rows, never targets — the cohort must be fitted the way a real
    low-history user would be, and scored on an item the fit never saw.
    """
    columns = list(TRAIN_COLUMNS)
    return pd.concat(
        [train, cohort.history[columns].astype(train[columns].dtypes.to_dict())],
        ignore_index=True,
    )


def expected_fallback_served(bucket: SyntheticColdSlice) -> int:
    """How many of a bucket's users ADR 0001's routing rule would send to fallback.

    All of them below ``COLD_START_THRESHOLD``, none at or above it. This is the
    *contract*, deliberately derived from the threshold rather than from what any
    particular model does, so that a model whose fallback boundary sits somewhere
    else shows up as a mismatch instead of defining its own pass mark.
    """
    return bucket.n_users if bucket.history_size < COLD_START_THRESHOLD else 0


def routing_is_correct(result: EvalResult) -> bool:
    """True iff every bucket's measured fallback count matches the contract."""
    slices = result.synthetic_cold_slices
    if not slices:
        return False
    return all(
        bucket.n_fallback_served == expected_fallback_served(bucket) for bucket in slices.values()
    )


def metrics(result: EvalResult, *, suffix: str) -> dict[str, float]:
    """Per-bucket MLflow metrics for one evaluation."""
    out: dict[str, float] = {}
    for history_size, bucket in sorted(result.synthetic_cold_slices.items()):
        out[f"{METRIC_PREFIX}_recall_{suffix}_h{history_size}"] = bucket.metrics.recall
        out[f"{METRIC_PREFIX}_ndcg_{suffix}_h{history_size}"] = bucket.metrics.ndcg
        out[f"{METRIC_PREFIX}_n_users_h{history_size}"] = float(bucket.n_users)
        if bucket.n_fallback_served is not None:
            out[f"{METRIC_PREFIX}_fallback_served_h{history_size}"] = float(
                bucket.n_fallback_served
            )
            out[f"{METRIC_PREFIX}_expected_fallback_served_h{history_size}"] = float(
                expected_fallback_served(bucket)
            )
    return out


def params(cohort: SyntheticColdCohort) -> dict[str, str | int]:
    """Provenance params, so a run says which cohort produced its numbers."""
    provenance = cohort.provenance
    return {
        "synth_cold_seed": provenance.seed,
        "synth_cold_generator_version": provenance.generator_version,
        "synth_cold_data_version": provenance.data_version,
        "synth_cold_fingerprint": provenance.fingerprint,
        "synth_cold_cutoff": provenance.split_cutoff,
        "synth_cold_users_per_bucket": provenance.users_per_bucket,
        "n_synth_cold_users": cohort.n_users,
        "n_synth_cold_history_rows": len(cohort.history),
    }


def log_summary(result: EvalResult, *, logger: logging.Logger, k: int) -> None:
    """Print the buckets the way a reader wants to read them: one line each.

    A mismatched fallback count is logged as a warning rather than raised. The
    cohort's job is to make the routing claim falsifiable and visible, not to
    stop a training run — the run's numbers are still the numbers, and
    ``synth_cold_routing_ok`` on the MLflow run is what a reader filters on.
    """
    for history_size, bucket in sorted(result.synthetic_cold_slices.items()):
        expected = expected_fallback_served(bucket)
        logger.info(
            "Synth cold h%-2d (n=%d): recall@%d=%.4f ndcg@%d=%.4f fallback-served=%s (expected %d)",
            history_size,
            bucket.n_users,
            k,
            bucket.metrics.recall,
            k,
            bucket.metrics.ndcg,
            "unmeasured" if bucket.n_fallback_served is None else bucket.n_fallback_served,
            expected,
        )
        if bucket.n_fallback_served is not None and bucket.n_fallback_served != expected:
            logger.warning(
                "Routing mismatch at h%d: %d of %d users went to the popularity fallback, "
                "ADR 0001's threshold of %d says %d should have. Logged as %s=false.",
                history_size,
                bucket.n_fallback_served,
                bucket.n_users,
                COLD_START_THRESHOLD,
                expected,
                ROUTING_TAG,
            )
