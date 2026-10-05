"""The softmax losses ADR 0020 adds to SASRec, and the sampler they train with (WO-4).

v1 trains with binary cross-entropy against 32 sampled negatives. ADR 0020's cells
replace that with one of two softmax objectives:

* **sampled softmax** — the positive competes against ``negative_count`` (1,024 in
  the cells) uniformly sampled negatives in one cross-entropy;
* **full softmax** — the positive competes against every movie in the catalog
  (34,461 on the full split). This is the ceiling the sampled loss approximates.

Both score a candidate by the same raw dot product v1 trains on: the unnormalized
encoder output against the unnormalized item embedding. Retrieval still searches
the L2-normalized vectors, exactly as it does for v1.

**What a negative may not be.** The rule is ADR 0016's and v1's: never the padding
id, never the target, and never an item in the history the encoder read for that
example. The full softmax applies the same rule as a mask, so it is literally the
sampled softmax with every eligible negative drawn — which is what makes the two
agree exactly on a catalog small enough to enumerate
(``tests/unit/test_sasrec_softmax_objectives.py``). The target itself is never
masked, even in the rare case it also appears in its own history.

**No log-Q correction.** Sampled softmax normally subtracts ``log Q(item)`` from
each sampled logit so a non-uniform proposal does not bias the estimate. Every
eligible item here has the same proposal probability, so the correction is one
constant added to every logit in the row, and a softmax is unchanged by that.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
import torch.nn.functional as F  # noqa: N812

BCE_LOSSES = ("gbce", "bce")
SAMPLED_SOFTMAX_LOSS = "sampled-softmax"
FULL_SOFTMAX_LOSS = "full-softmax"
SOFTMAX_LOSSES = (SAMPLED_SOFTMAX_LOSS, FULL_SOFTMAX_LOSS)
LOSSES = BCE_LOSSES + SOFTMAX_LOSSES

# The per-call ceiling on the boolean table the sampler builds (rows x catalog).
# 32 MiB covers a 512-example step at 34,461 movies in one piece; larger steps are
# sampled in row blocks, which changes nothing but how many arrays exist at once.
_TABLE_BYTES = 2**25


def sample_uniform_negatives(
    forbidden: np.ndarray[Any, Any],
    *,
    n_items: int,
    count: int,
    rng: np.random.Generator,
) -> np.ndarray[Any, np.dtype[np.int64]]:
    """Draw ``count`` distinct negatives per row, uniformly from what the row allows.

    ``forbidden``: ``(rows, width)`` dense ids that row may not use — its history
    window and its target. Zero is padding and is ignored; ids run ``1..n_items``.
    Returns ``(rows, count)`` int64.

    Each row's result is a uniformly random ``count``-subset of
    ``{1..n_items} \\ forbidden[row]``, in random order. The method is v1's —
    draw uniformly, reject forbidden ids and repeats, keep the first ``count``
    survivors — run for every row at once instead of in a Python loop per
    example. Keeping the first survivors of an i.i.d. stream is sequential
    rejection sampling, so the subset is uniform; a row that runs out of draws is
    redrawn whole with twice the margin, and because that redraw is triggered
    by an event that treats every eligible id alike, it leaves the result
    uniform too.

    The random stream comes only from ``rng`` and the work is plain NumPy, so a
    seed gives the same negatives on every device: a CPU run and an ``mps`` run
    of one configuration see identical draws for identical batches.
    """
    forbidden = np.asarray(forbidden, dtype=np.int64)
    if forbidden.ndim != 2:
        raise ValueError("forbidden must be a (rows, width) array")
    if count <= 0:
        raise ValueError("count must be positive")
    rows = forbidden.shape[0]
    output = np.empty((rows, count), dtype=np.int64)
    if rows == 0:
        return output
    if forbidden.size and (forbidden.min() < 0 or forbidden.max() > n_items):
        raise ValueError("forbidden ids must lie in [0, n_items]")
    block = max(1, _TABLE_BYTES // (n_items + 1))
    for start in range(0, rows, block):
        stop = min(rows, start + block)
        output[start:stop] = _sample_block(
            forbidden[start:stop], n_items=n_items, count=count, rng=rng
        )
    return output


def _sample_block(
    forbidden: np.ndarray[Any, np.dtype[np.int64]],
    *,
    n_items: int,
    count: int,
    rng: np.random.Generator,
) -> np.ndarray[Any, np.dtype[np.int64]]:
    rows = forbidden.shape[0]
    stride = n_items + 1
    row_base = np.arange(rows, dtype=np.int64)[:, None] * stride
    # One flag per (row, item): True where that row may not use the item. Column
    # 0 is padding; it is flagged by the zeros in ``forbidden`` but never drawn.
    blocked = np.zeros(rows * stride, dtype=bool)
    blocked[(forbidden + row_base).ravel()] = True
    eligible = n_items - blocked.reshape(rows, stride)[:, 1:].sum(axis=1)
    if (eligible < count).any():
        raise ValueError("not enough eligible unique negatives for requested count")

    output = np.empty((rows, count), dtype=np.int64)
    pending = np.arange(rows)
    # Expected rejections per row are about count**2 / (2 * n_items) repeats plus
    # count * width / n_items forbidden hits; an eighth of ``count`` plus a floor
    # leaves a short row vanishingly rare at the cells' sizes, and harmless.
    margin = count // 8 + 32
    while len(pending):
        width = count + margin
        draws = rng.integers(1, n_items + 1, size=(len(pending), width))
        invalid = blocked[(draws + row_base[pending]).ravel()].reshape(draws.shape)
        # A repeat is any draw whose value an earlier slot in the same row already
        # holds. A stable sort keeps equal values in slot order, so after it every
        # equal-value run starts with the earliest slot, which is the one kept.
        # Catalog ids fit 16 bits on MovieLens 25M, where NumPy's stable sort is a
        # radix sort; a larger catalog falls back to the general stable sort.
        sortable = draws.astype(np.uint16) if n_items < 2**16 else draws
        order = np.argsort(sortable, axis=1, kind="stable")
        ordered = np.take_along_axis(draws, order, axis=1)
        repeat_sorted = np.zeros_like(invalid)
        repeat_sorted[:, 1:] = ordered[:, 1:] == ordered[:, :-1]
        repeat = np.empty_like(invalid)
        np.put_along_axis(repeat, order, repeat_sorted, axis=1)
        valid = ~(invalid | repeat)
        keep = valid & (np.cumsum(valid, axis=1) <= count)
        complete = keep.sum(axis=1) == count
        output[pending[complete]] = draws[complete][keep[complete]].reshape(-1, count)
        pending = pending[~complete]
        margin *= 2
    return output


def forbidden_ids(histories: torch.Tensor, positives: torch.Tensor) -> np.ndarray[Any, Any]:
    """``(rows, length)`` histories and ``(rows,)`` targets -> the ids each row may not draw."""
    return torch.cat([histories, positives.unsqueeze(1)], dim=1).cpu().numpy()


def causal_prefixes(
    sequences: torch.Tensor,
    prediction_windows: torch.Tensor,
    prediction_positions: torch.Tensor,
) -> torch.Tensor:
    """Each all-positions target's causal prefix as a padded ``(targets, length)`` row.

    A target predicted at position ``p`` of its window has read positions ``0..p``
    and nothing after; later positions are zeroed so they count as padding. This
    is the history ``sample_position_negatives`` excludes, built for every target
    at once.
    """
    windows = sequences[prediction_windows]
    length = sequences.shape[1]
    visible = torch.arange(length, device=sequences.device).unsqueeze(0) <= (
        prediction_positions.unsqueeze(1)
    )
    return torch.where(visible, windows, torch.zeros_like(windows))


def sampled_softmax_loss(
    positive_logits: torch.Tensor, negative_logits: torch.Tensor
) -> torch.Tensor:
    """Mean cross-entropy of the positive against its sampled negatives.

    ``positive_logits``: ``(rows,)``; ``negative_logits``: ``(rows, count)``. Per row
    this is ``logsumexp([positive, negatives]) - positive``.
    """
    logits = torch.cat([positive_logits.unsqueeze(1), negative_logits], dim=1)
    target = torch.zeros(len(logits), dtype=torch.long, device=logits.device)
    return F.cross_entropy(logits, target)


def full_softmax_loss(
    user_vectors: torch.Tensor,
    item_vectors: torch.Tensor,
    positives: torch.Tensor,
    histories: torch.Tensor,
) -> torch.Tensor:
    """Mean cross-entropy of the positive against every movie its history allows.

    ``user_vectors``: ``(rows, d)``. ``item_vectors``: ``(n_items, d)``, row ``i``
    holding dense id ``i + 1`` (padding and the unknown token are not candidates).
    ``positives``: ``(rows,)`` dense ids. ``histories``: ``(rows, length)`` dense
    ids, zero for padding.

    Logits are ``rows x n_items`` — 70 MB at 512 rows and 34,461 movies — so the
    microbatch setting is what bounds this loss's memory, not the catalog.
    """
    rows = user_vectors.shape[0]
    n_items = item_vectors.shape[0]
    logits = user_vectors @ item_vectors.T
    blocked = torch.zeros(rows, n_items + 1, dtype=torch.bool, device=logits.device)
    blocked.scatter_(1, histories, True)
    blocked.scatter_(1, positives.unsqueeze(1), False)
    logits = logits.masked_fill(blocked[:, 1:], float("-inf"))
    return F.cross_entropy(logits, positives - 1)
