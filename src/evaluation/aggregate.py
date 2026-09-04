"""Averaging several runs of the same model into one `EvalResult`.

`src/evaluation/gate.py` compares two results and returns a verdict. When the
model behind a result has a stochastic component, one run is a sample and the
verdict inherits its noise — and on 2026-08-30 that stopped being theoretical:
the LightGBM ranker's *overall* NDCG@10 moved 5.81% across three seeds, wider
than ADR 0001's own +3% promotion threshold, so a single seeded run could not
establish the improvement the gate is asked to confirm. The same comparison read
−4.16%, −17.24% and +13.59% on the warm slice depending only on which seed both
sides happened to be run at.

Gating on the mean of several seeded runs is the interim answer (the structural
one, a training sample large enough that the seed stops choosing the training
set, landed alongside this module). `mean_eval_result` is that mean, and it is
deliberately strict about what it will average: two results that disagree about
K, or about how many users are in a slice, are not two samples of one quantity
and averaging them would produce a number with no referent.
"""

from __future__ import annotations

from collections.abc import Sequence

from .protocol import EvalResult, UserMetrics


class MismatchedResultsError(ValueError):
    """The results handed in are not repeated measurements of the same thing.

    Sibling of `gate.GateInputError` and raised for the same reason — the inputs
    do not describe one question — but kept separate because averaging and
    gating fail at different points and a caller doing both wants to know which
    step refused.
    """


def mean_eval_result(results: Sequence[EvalResult]) -> EvalResult:
    """Average repeated runs of one model into a single `EvalResult`.

    Args:
        results: two or more `EvalResult`s from the same model over the same
            holdout, differing only in seed. A single result is returned
            unchanged, which keeps `mean_eval_result([r])` meaningful for a
            deterministic model rather than a special case at every call site.

    Returns:
        An `EvalResult` whose six metrics are the arithmetic means of the
        inputs', carrying the shared `k` and the shared slice sizes.

    Raises:
        MismatchedResultsError: no results were given, or they disagree about
            `k` or about a slice's user count.

    `synthetic_cold_slices` is deliberately not averaged. ADR 0011's buckets are
    reported per run and are not part of a promotion decision — the same line
    `gate.eval_result_from_mlflow_run` draws — so the returned result carries
    none rather than an average nobody asked for.
    """
    if not results:
        raise MismatchedResultsError("no results to average")
    if len(results) == 1:
        return results[0]

    ks = {r.k for r in results}
    if len(ks) > 1:
        raise MismatchedResultsError(
            f"results were scored at different K values ({sorted(ks)}); a mean across them "
            "would answer a question nobody asked (see EvalResult.k)"
        )
    for field in ("n_warm_users", "n_cold_users"):
        counts = {getattr(r, field) for r in results}
        if len(counts) > 1:
            raise MismatchedResultsError(
                f"results disagree about {field} ({sorted(counts)}); repeated runs of one "
                "model over one holdout cannot, so these are not the same measurement"
            )

    return EvalResult(
        warm=_mean_metrics([r.warm for r in results]),
        cold=_mean_metrics([r.cold for r in results]),
        overall=_mean_metrics([r.overall for r in results]),
        n_warm_users=results[0].n_warm_users,
        n_cold_users=results[0].n_cold_users,
        k=results[0].k,
    )


def _mean_metrics(metrics: Sequence[UserMetrics]) -> UserMetrics:
    n = len(metrics)
    return UserMetrics(
        recall=sum(m.recall for m in metrics) / n,
        ndcg=sum(m.ndcg for m in metrics) / n,
    )
