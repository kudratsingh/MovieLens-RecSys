from __future__ import annotations

import pytest

from src.evaluation.sasrec_latency import summarize_latency_ns


def test_latency_summary_uses_millisecond_quantiles() -> None:
    summary = summarize_latency_ns([1_000_000, 2_000_000, 3_000_000, 4_000_000])

    assert summary.mean_ms == pytest.approx(2.5)
    assert summary.p50_ms == pytest.approx(2.5)
    assert summary.p95_ms == pytest.approx(3.85)
    assert summary.p99_ms == pytest.approx(3.97)
    assert summary.max_ms == pytest.approx(4.0)


@pytest.mark.parametrize("samples", [[], [0], [-1], [1.5]])
def test_latency_summary_rejects_invalid_samples(samples: list[object]) -> None:
    with pytest.raises(ValueError):
        summarize_latency_ns(samples)  # type: ignore[arg-type]
