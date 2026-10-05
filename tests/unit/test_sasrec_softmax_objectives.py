"""ADR 0020's correctness checks for the softmax losses and their sampler (WO-4).

ADR 0020 gates every cell on the same battery ADR 0016 used: causal mask,
padding, target exclusion, sampler probabilities, sampled and full loss agreeing
on a tiny frame, save-and-reload, and memorization. The encoder-level forms of
the first two live in ``test_sasrec_transformer.py``; here each check is run
through the new losses, because a loss can break a property the encoder keeps —
the full softmax's history mask, for one, is built per target and could leak a
later movie into an earlier prediction. These are correctness checks, never
quality evidence.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
import torch.nn.functional as F  # noqa: N812

from src.evaluation.metrics import recall_at_k
from src.models.candidates.sasrec import SASRecConfig, SASRecEncoder, SASRecModel
from src.models.candidates.sasrec_artifact import (
    MANIFEST_FILENAME,
    SASRecArtifactManifest,
    export_sasrec,
    load_sasrec,
)
from src.models.candidates.sasrec_objectives import (
    causal_prefixes,
    forbidden_ids,
    full_softmax_loss,
    sample_uniform_negatives,
    sampled_softmax_loss,
)
from src.models.candidates.sequence_data import build_strict_prefix_example_store
from src.training.sasrec import encoder_weights_sha256

SOFTMAX_LOSSES = ("sampled-softmax", "full-softmax")
HISTORY_LENGTHS = (1, 3, 12, 49, 50)


def _config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": 50,
        "hidden_dim": 16,
        "num_blocks": 2,
        "num_heads": 2,
        "feedforward_dim": 32,
        "dropout": 0.0,
        "negative_count": 8,
        "loss": "sampled-softmax",
        "batch_size": 32,
        "epochs": 1,
        "faiss_exact": True,
        "seed": 42,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _logits(
    encoder: SASRecEncoder,
    histories: torch.Tensor,
    positives: torch.Tensor,
    negatives: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    users = encoder.training_user_vectors(histories)
    positive = (users * encoder.item_vectors(positives, normalize=False)).sum(dim=1)
    negative = torch.einsum("bd,bkd->bk", users, encoder.item_vectors(negatives, normalize=False))
    return users, positive, negative


def _left_padded(history: list[int], length: int) -> torch.Tensor:
    row = torch.zeros((1, length), dtype=torch.long)
    row[0, length - len(history) :] = torch.tensor(history)
    return row


# --- sampler probabilities and target exclusion ---------------------------------


def test_the_sampler_draws_each_allowed_movie_with_equal_probability() -> None:
    """Inclusion rates match count / eligible per movie, and pairs match too.

    A uniformly random k-subset of E includes each movie with probability k/|E|
    and each pair with k(k-1)/(|E|(|E|-1)). The pair rate is what separates a
    uniform subset from schemes that get single rates right (a fixed stride,
    say). Tolerances are five standard errors.
    """
    n_items, count, rows = 30, 6, 20_000
    forbidden = np.zeros((rows, 4), dtype=np.int64)
    forbidden[:, :3] = [2, 9, 17]  # a history
    forbidden[:, 3] = 25  # the target
    sampled = sample_uniform_negatives(
        forbidden, n_items=n_items, count=count, rng=np.random.default_rng(0)
    )

    eligible = sorted(set(range(1, n_items + 1)) - {2, 9, 17, 25})
    single = count / len(eligible)
    pair = count * (count - 1) / (len(eligible) * (len(eligible) - 1))
    included = np.zeros((rows, n_items + 1), dtype=bool)
    np.put_along_axis(included, sampled, True, axis=1)

    assert not included[:, [0, 2, 9, 17, 25]].any()
    rates = included[:, eligible].mean(axis=0)
    assert np.all(np.abs(rates - single) < 5 * np.sqrt(single * (1 - single) / rows))
    for left, right in itertools.combinations(eligible[:8], 2):
        rate = (included[:, left] & included[:, right]).mean()
        assert abs(rate - pair) < 5 * np.sqrt(pair * (1 - pair) / rows)


def test_the_sampler_excludes_padding_history_target_and_repeats_row_by_row() -> None:
    rng = np.random.default_rng(1)
    histories = torch.zeros((64, 20), dtype=torch.long)
    for row in range(64):
        length = int(rng.integers(0, 21))
        if length:
            histories[row, 20 - length :] = torch.from_numpy(
                rng.choice(np.arange(1, 201), size=length, replace=False)
            )
    positives = torch.from_numpy(rng.integers(1, 201, size=64))
    sampled = sample_uniform_negatives(
        forbidden_ids(histories, positives), n_items=200, count=150, rng=rng
    )

    for history, positive, row in zip(histories.tolist(), positives.tolist(), sampled.tolist()):
        assert len(set(row)) == 150
        assert 0 not in row and positive not in row
        assert not set(row) & set(history)
        assert all(1 <= item <= 200 for item in row)


def test_the_sampler_takes_every_allowed_movie_when_asked_for_all_of_them() -> None:
    """Asking for exactly the eligible count exercises the short-row redraw."""
    forbidden = np.array([[1, 2, 7, 8], [3, 4, 5, 6], [0, 0, 0, 9]])
    sampled = sample_uniform_negatives(
        forbidden[:2], n_items=10, count=6, rng=np.random.default_rng(2)
    )

    assert sorted(sampled[0].tolist()) == [3, 4, 5, 6, 9, 10]
    assert sorted(sampled[1].tolist()) == [1, 2, 7, 8, 9, 10]
    with pytest.raises(ValueError, match="not enough eligible"):
        sample_uniform_negatives(forbidden, n_items=10, count=7, rng=np.random.default_rng(4))


def test_the_sampler_is_seeded() -> None:
    forbidden = np.array([[1, 2, 3], [4, 5, 0]])

    def draw(seed: int) -> np.ndarray:
        return sample_uniform_negatives(
            forbidden, n_items=500, count=40, rng=np.random.default_rng(seed)
        )

    assert np.array_equal(draw(5), draw(5))
    assert not np.array_equal(draw(5), draw(6))


def test_the_full_softmax_masks_the_history_but_never_the_target() -> None:
    torch.manual_seed(0)
    users = torch.randn(2, 4)
    items = torch.randn(6, 4)
    positives = torch.tensor([2, 5])
    # Row 1's target is also in its own history: it must stay a candidate.
    histories = torch.tensor([[0, 1, 3], [5, 6, 4]])

    loss = full_softmax_loss(users, items, positives, histories)

    logits = users @ items.T
    allowed = [[1, 3, 4, 5], [0, 1, 2, 4]]  # zero-based columns: dense ids 2,4,5,6 and 1,2,3,5
    expected = torch.stack(
        [
            torch.logsumexp(logits[row, allowed[row]], dim=0) - logits[row, positives[row] - 1]
            for row in range(2)
        ]
    ).mean()
    torch.testing.assert_close(loss, expected)


# --- sampled and full agree on a tiny frame -----------------------------------


def _tiny_batch() -> tuple[SASRecEncoder, torch.Tensor, torch.Tensor, int]:
    rng = np.random.default_rng(7)
    rows = []
    for user in range(1, 13):
        length = int(rng.integers(2, 9))
        watched = rng.choice(np.arange(100, 124), size=length, replace=False)
        rows.extend((user, int(item), step) for step, item in enumerate(watched))
    train = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    items = sorted(train["movieId"].unique())
    store = build_strict_prefix_example_store(
        train, item_to_index={item: i + 1 for i, item in enumerate(items)}, max_length=8
    )
    histories, positives = store.batch(torch.arange(len(store)))
    torch.manual_seed(3)
    encoder = SASRecEncoder(len(items) + 2, _config(max_sequence_length=8)).train()
    return encoder, histories, positives, len(items)


def test_sampled_softmax_over_every_allowed_movie_is_the_full_softmax() -> None:
    """The full softmax is the sampled softmax with nothing left unsampled.

    On a catalog small enough to enumerate, give each example every movie its
    history allows as negatives. The two losses — and their gradients — must
    then agree to float rounding. Any difference would mean the two objectives
    disagree about what a negative is.
    """
    encoder, histories, positives, n_items = _tiny_batch()
    users = encoder.training_user_vectors(histories)
    full = full_softmax_loss(
        users, encoder.item_embedding.weight[1 : n_items + 1], positives, histories
    )
    full_grad = torch.autograd.grad(full, encoder.item_embedding.weight)[0]

    per_row = []
    users = encoder.training_user_vectors(histories)
    for row in range(len(positives)):
        allowed = sorted(
            set(range(1, n_items + 1)) - set(histories[row].tolist()) - {int(positives[row])}
        )
        _u, positive, negative = _logits(
            encoder,
            histories[row : row + 1],
            positives[row : row + 1],
            torch.tensor([allowed]),
        )
        per_row.append(sampled_softmax_loss(positive, negative))
    sampled = torch.stack(per_row).mean()
    sampled_grad = torch.autograd.grad(sampled, encoder.item_embedding.weight)[0]

    torch.testing.assert_close(sampled, full, rtol=1e-6, atol=1e-6)
    torch.testing.assert_close(sampled_grad, full_grad, rtol=1e-5, atol=1e-7)


def test_more_negatives_move_the_sampled_loss_up_toward_the_full_loss() -> None:
    encoder, histories, positives, n_items = _tiny_batch()
    row = 0
    allowed = sorted(
        set(range(1, n_items + 1)) - set(histories[row].tolist()) - {int(positives[row])}
    )
    with torch.no_grad():
        users = encoder.training_user_vectors(histories[:1])
        full = full_softmax_loss(
            users, encoder.item_embedding.weight[1 : n_items + 1], positives[:1], histories[:1]
        )
        losses = []
        for size in (1, 4, 8, len(allowed)):
            _u, positive, negative = _logits(
                encoder, histories[:1], positives[:1], torch.tensor([allowed[:size]])
            )
            losses.append(float(sampled_softmax_loss(positive, negative)))

    assert losses == sorted(losses)
    assert losses[-1] == pytest.approx(float(full), rel=1e-6)


# --- causal mask and padding, through the losses -------------------------------


@pytest.mark.parametrize("loss", SOFTMAX_LOSSES)
def test_a_later_movie_cannot_change_an_earlier_targets_loss(loss: str) -> None:
    """All-positions: changing position 5 leaves the loss at position 3 untouched.

    The full softmax builds a history mask per target; this is the test that the
    mask is the causal prefix and not the whole window.
    """
    torch.manual_seed(1)
    n_items = 30
    encoder = SASRecEncoder(n_items + 2, _config(max_sequence_length=6)).eval()
    first = torch.tensor([[0, 4, 9, 11, 7, 20]])
    later = torch.tensor([[0, 4, 9, 11, 7, 3]])
    windows = torch.tensor([0])
    positions = torch.tensor([3])
    target = torch.tensor([20])  # in the first window, but only after position 3

    def loss_at_three(sequences: torch.Tensor) -> torch.Tensor:
        users = encoder.encode_positions(sequences)[windows, positions]
        prefixes = causal_prefixes(sequences, windows, positions)
        if loss == "full-softmax":
            return full_softmax_loss(
                users, encoder.item_embedding.weight[1 : n_items + 1], target, prefixes
            )
        negatives = torch.tensor([[1, 2, 3, 5, 6]])
        positive = (users * encoder.item_vectors(target, normalize=False)).sum(dim=1)
        negative = torch.einsum(
            "bd,bkd->bk", users, encoder.item_vectors(negatives, normalize=False)
        )
        return sampled_softmax_loss(positive, negative)

    with torch.no_grad():
        assert torch.equal(loss_at_three(first), loss_at_three(later))
        assert torch.equal(
            causal_prefixes(first, windows, positions), torch.tensor([[0, 4, 9, 11, 0, 0]])
        )


@pytest.mark.parametrize("loss", SOFTMAX_LOSSES)
@pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
def test_padded_histories_give_finite_losses_and_never_train_the_padding_row(
    loss: str, history_length: int
) -> None:
    torch.manual_seed(2)
    n_items = 80
    encoder = SASRecEncoder(n_items + 2, _config(dropout=0.2)).train()
    histories = _left_padded(list(range(1, history_length + 1)), 50)
    positives = torch.tensor([n_items])
    users = encoder.training_user_vectors(histories)
    if loss == "full-softmax":
        value = full_softmax_loss(
            users, encoder.item_embedding.weight[1 : n_items + 1], positives, histories
        )
    else:
        negatives = torch.from_numpy(
            sample_uniform_negatives(
                forbidden_ids(histories, positives),
                n_items=n_items,
                count=16,
                rng=np.random.default_rng(0),
            )
        )
        positive = (users * encoder.item_vectors(positives, normalize=False)).sum(dim=1)
        negative = torch.einsum(
            "bd,bkd->bk", users, encoder.item_vectors(negatives, normalize=False)
        )
        value = sampled_softmax_loss(positive, negative)
    value.backward()  # type: ignore[no-untyped-call]

    assert torch.isfinite(value)
    for name, parameter in encoder.named_parameters():
        assert parameter.grad is not None and torch.isfinite(parameter.grad).all(), name
    assert torch.count_nonzero(encoder.item_embedding.weight.grad[0]) == 0  # type: ignore[index]


# --- save-and-reload --------------------------------------------------------------


@pytest.mark.parametrize("loss", SOFTMAX_LOSSES)
def test_a_softmax_trained_model_saves_and_reloads_exactly(loss: str, tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    rows = []
    for user in range(1, 31):
        watched = rng.choice(np.arange(10, 70), size=int(rng.integers(4, 15)), replace=False)
        rows.extend((user, int(item), step) for step, item in enumerate(watched))
    train = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    config = _config(
        loss=loss,
        max_sequence_length=10,
        epochs=3,
        microbatch_size=8,
        early_stopping=True,
        early_stopping_min_epochs=2,
        early_stopping_probe_fraction=0.2,
    )
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        train, retrieval_backend="torch"
    )
    manifest = export_sasrec(model, tmp_path / "run")
    reloaded = load_sasrec(tmp_path / "run" / MANIFEST_FILENAME)

    assert SASRecArtifactManifest.load(tmp_path / "run" / MANIFEST_FILENAME).loss == loss
    assert manifest.loss == loss
    assert reloaded.config == config
    assert model._encoder is not None and reloaded._encoder is not None
    assert encoder_weights_sha256(reloaded._encoder.state_dict()) == encoder_weights_sha256(
        model._encoder.state_dict()
    )
    for normalized_a, normalized_b in zip(model.item_matrices(), reloaded.item_matrices()):
        assert np.array_equal(normalized_a, normalized_b)
    history = [int(item) for item in train[train["userId"] == 1]["movieId"]]
    assert model.recommend_from_history(history, 10) == reloaded.recommend_from_history(history, 10)


# --- memorization ---------------------------------------------------------------


@pytest.mark.parametrize("loss", SOFTMAX_LOSSES)
def test_memorization_recovers_a_deterministic_next_movie(loss: str) -> None:
    """The 40-movie cycle from ``test_sasrec_transformer``, trained on each new loss."""
    catalog = 40
    rows: list[tuple[int, int, int]] = []
    targets: dict[int, set[int]] = {}
    for user in range(1, 81):
        start = (user * 3) % catalog
        rows.extend((user, 100 + (start + step) % catalog, step) for step in range(8))
        targets[user] = {100 + (start + 8) % catalog}
    config = _config(
        loss=loss,
        max_sequence_length=10,
        hidden_dim=32,
        feedforward_dim=64,
        negative_count=8,
        batch_size=64,
        epochs=60,
    )
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"]),
        retrieval_backend="torch",
    )

    recall = np.mean(
        [recall_at_k(target, model.recommend(user, 10), 10) for user, target in targets.items()]
    )
    assert recall >= 0.95


def test_the_sampled_loss_is_the_cross_entropy_of_the_positive() -> None:
    positive = torch.tensor([2.0, -1.0])
    negative = torch.tensor([[1.0, 0.5, -3.0], [0.0, 0.0, 4.0]])
    expected = torch.stack(
        [
            torch.logsumexp(torch.cat([positive[row : row + 1], negative[row]]), 0) - positive[row]
            for row in range(2)
        ]
    ).mean()
    torch.testing.assert_close(sampled_softmax_loss(positive, negative), expected)
    torch.testing.assert_close(
        sampled_softmax_loss(positive, negative),
        F.cross_entropy(torch.cat([positive[:, None], negative], 1), torch.zeros(2).long()),
    )
