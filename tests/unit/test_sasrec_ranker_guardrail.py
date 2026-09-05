from __future__ import annotations

import pytest

from src.evaluation.protocol import EvalResult, UserMetrics
from src.training.sasrec_ranker_guardrail import (
    _copy_new,
    require_published_reference,
    require_reference_training_shape,
    result_metrics,
)


def _result() -> EvalResult:
    return EvalResult(
        warm=UserMetrics(recall=0.4, ndcg=0.2),
        cold=UserMetrics(recall=0.5, ndcg=0.3),
        overall=UserMetrics(recall=0.45, ndcg=0.25),
        n_warm_users=10,
        n_cold_users=5,
        k=10,
    )


def test_reference_reproduction_requires_all_six_published_metrics() -> None:
    metrics = result_metrics(_result())

    require_published_reference(_result(), metrics)

    metrics["warm_ndcg_at_k"] += 1e-6
    with pytest.raises(RuntimeError, match="does not reproduce"):
        require_published_reference(_result(), metrics)


def test_reference_reproduction_rejects_missing_metric() -> None:
    metrics = result_metrics(_result())
    del metrics["cold_recall_at_k"]

    with pytest.raises(ValueError, match="missing metrics"):
        require_published_reference(_result(), metrics)


def test_reference_training_shape_is_fail_closed() -> None:
    require_reference_training_shape(154_003, 87_794, 1_843_674)

    with pytest.raises(RuntimeError, match="training shape"):
        require_reference_training_shape(154_003, 87_793, 1_843_674)


def test_recovery_copy_preserves_bytes_and_refuses_overwrite(tmp_path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "retained" / "ranker.txt"
    source.write_bytes(b"immutable-ranker")

    _copy_new(source, destination)

    assert destination.read_bytes() == source.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        _copy_new(source, destination)
