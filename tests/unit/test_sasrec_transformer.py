"""WO-2: the hand-written Transformer, held to the packaged one it replaces.

Five claims, in the order the work order lists them:

1. **Rule D7** — nothing under ``src/`` uses PyTorch's packaged Transformer,
   attention or layer-norm code (``test_src_uses_no_packaged_transformer_code``).
2. **Equivalence** — weights copied out of ``nn.TransformerEncoder`` (kept in
   ``tests/`` only, in ``legacy_sasrec_encoder.py``) through the artifact
   converter produce the same outputs within ``1e-5`` for left-padded histories
   of length 1, 3, 12, 49 and 50, in ``eval()`` mode and in ``train()`` mode with
   dropout 0. Gradients match too, and a fresh encoder equals a fresh v1 encoder
   bit for bit at the same seed.
3. **Every saved model keeps loading** — a pre-WO-2 archive loads through
   ``load_sasrec`` and retrieves what the old encoder retrieved. The pinned
   full-data artifact is checked only where it exists (env-gated, see the end).
4. **Behaviour** — memorization, a gradient for every weight, causality, and
   padded positions that are exact zeros and never NaN.
5. The pilot (done-criterion 5) is a training run and lives in
   ``docs/experiments/sasrec/wo2-handwritten-pilot-6pct.json``, not here.

The tolerance is absolute on outputs that come out of a final layer norm, so
they are of order one. What it measures is float32 rounding from evaluating the
same function with different kernels; the tests below observe about ``1e-6``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import torch

from src.evaluation.metrics import recall_at_k
from src.models.candidates.sasrec import (
    SASRecConfig,
    SASRecEncoder,
    SASRecModel,
    sample_negatives,
    sampled_gbce_loss,
)
from src.models.candidates.sasrec_artifact import (
    ENCODER_IMPL_HAND_WRITTEN,
    ENCODER_IMPL_LEGACY,
    MANIFEST_FILENAME,
    SASRecArtifactManifest,
    export_sasrec,
    legacy_state_to_hand_written,
    load_sasrec,
)
from src.models.candidates.sequence_data import (
    StrictPrefixExamples,
    build_strict_prefix_example_store,
)
from src.models.candidates.transformer import attention_mask, scaled_dot_product
from tests.unit.legacy_sasrec_encoder import (
    LegacySASRecEncoder,
    fastpath,
    hand_written_state_to_legacy,
    legacy_encoder_from,
    rewrite_as_legacy_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HISTORY_LENGTHS = (1, 3, 12, 49, 50)
WINDOW = 50
TOLERANCE = 1e-5
CATALOG = 300

# Rule D7. Each pattern is a way to reach PyTorch's packaged Transformer,
# attention or layer-norm code, or the process-wide switch the old encoder
# needed. ``layer_norm`` and the private kernels are not in the brief's list;
# they are here because calling them would hand-write nothing.
BANNED_IN_SRC = {
    "nn.Transformer*": re.compile(r"\bnn\.Transformer"),
    "Transformer{Encoder,Decoder}{,Layer}": re.compile(r"\bTransformer(Encoder|Decoder)(Layer)?\b"),
    "MultiheadAttention": re.compile(r"\bMultiheadAttention\b"),
    "LayerNorm": re.compile(r"\bLayerNorm\b"),
    "functional layer_norm": re.compile(r"\blayer_norm\b"),
    "scaled_dot_product_attention": re.compile(r"scaled_dot_product_attention"),
    "multi_head_attention_forward": re.compile(r"multi_head_attention_forward"),
    "private attention kernels": re.compile(
        r"_native_multi_head_attention|_transformer_encoder_layer_fwd"
    ),
    "torch.nn.modules.transformer": re.compile(r"torch\.nn\.modules\.transformer"),
    "the global fast-path switch": re.compile(r"backends\.mha"),
}


def _config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": WINDOW,
        "hidden_dim": 64,
        "num_blocks": 2,
        "num_heads": 2,
        "feedforward_dim": 256,
        "dropout": 0.2,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _left_padded(length: int, *, rows: int = 4, seed: int = 0) -> torch.Tensor:
    generator = torch.Generator().manual_seed(seed + length)
    sequences = torch.zeros((rows, WINDOW), dtype=torch.long)
    sequences[:, WINDOW - length :] = torch.randint(1, CATALOG, (rows, length), generator=generator)
    return sequences


def _converted(legacy: LegacySASRecEncoder) -> SASRecEncoder:
    """A hand-written encoder carrying ``legacy``'s weights, via the artifact converter."""
    encoder = SASRecEncoder(legacy.item_embedding.num_embeddings, legacy.config)
    arrays = legacy_state_to_hand_written(
        legacy.state_arrays(), num_blocks=legacy.config.num_blocks
    )
    encoder.load_state_dict({name: torch.from_numpy(array) for name, array in arrays.items()})
    return encoder.train(legacy.training)


def _perturbed_legacy(config: SASRecConfig, *, seed: int) -> LegacySASRecEncoder:
    """A v1 encoder with every weight moved off its initial value.

    Fresh weights leave every layer norm at ``(1, 0)`` and every attention bias
    at zero, which would let a converter that dropped them pass. Noise at 0.1
    keeps activations in the range a trained model has.
    """
    torch.manual_seed(seed)
    legacy = LegacySASRecEncoder(CATALOG, config)
    generator = torch.Generator().manual_seed(seed + 1)
    with torch.no_grad():
        for parameter in legacy.parameters():
            parameter.add_(0.1 * torch.randn(parameter.shape, generator=generator))
        legacy.item_embedding.weight[0].zero_()
    return legacy


def _trained_model() -> SASRecModel:
    """A small model fitted with the real trainer, so its weights are learned ones."""
    rows = [
        (user, 1_000 + (user * 7 + step * 3) % (CATALOG - 2), step)
        for user in range(1, 121)
        for step in range(70)
    ]
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    frame = frame.drop_duplicates(subset=["userId", "movieId"], keep="first")
    config = _config(
        hidden_dim=32,
        feedforward_dim=128,
        dropout=0.0,
        negative_count=8,
        batch_size=128,
        epochs=3,
        faiss_exact=True,
        seed=42,
    )
    return SASRecModel(config=config, cold_start_threshold=None).fit(
        frame, retrieval_backend="torch"
    )


# --- 1. rule D7 --------------------------------------------------------------


def test_src_uses_no_packaged_transformer_code() -> None:
    hits = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {label}: {line.strip()}"
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for label, pattern in BANNED_IN_SRC.items()
        if pattern.search(line)
    ]

    assert not hits, "rule D7 forbids these under src/:\n" + "\n".join(hits)


def test_the_d7_patterns_would_catch_the_old_encoder() -> None:
    """A search that finds nothing proves something only if it can find something."""
    old = (REPO_ROOT / "tests" / "unit" / "legacy_sasrec_encoder.py").read_text(encoding="utf-8")
    caught = {label for label, pattern in BANNED_IN_SRC.items() if pattern.search(old)}

    assert {
        "nn.Transformer*",
        "LayerNorm",
        "the global fast-path switch",
    } <= caught


# --- 2. equivalence with the packaged encoder ----------------------------------


@pytest.mark.parametrize(
    "changes",
    [{}, {"num_blocks": 1}, {"num_blocks": 4, "num_heads": 4}, {"hidden_dim": 32}],
    ids=["v1-shape", "one-block", "four-blocks-four-heads", "width-32"],
)
@pytest.mark.parametrize("seed", [0, 42])
def test_a_fresh_encoder_is_a_fresh_v1_encoder_bit_for_bit(
    changes: dict[str, int], seed: int
) -> None:
    """Same seed, same draws in the same order: initialization is not a source of drift."""
    config = _config(**changes)
    torch.manual_seed(seed)
    legacy = LegacySASRecEncoder(CATALOG, config)
    legacy_rng = torch.get_rng_state()
    torch.manual_seed(seed)
    hand_written = SASRecEncoder(CATALOG, config)

    converted = legacy_state_to_hand_written(legacy.state_arrays(), num_blocks=config.num_blocks)
    state = hand_written.state_dict()
    assert set(converted) == set(state)
    for name, tensor in state.items():
        assert torch.equal(torch.from_numpy(converted[name]), tensor), name
    # The same number of draws, so whatever runs next — the training permutation,
    # the first dropout mask — starts from the same generator state.
    assert torch.equal(torch.get_rng_state(), legacy_rng)


@pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_eval_outputs_match_the_packaged_encoder(history_length: int, seed: int) -> None:
    """``eval()`` mode, every position of every row, padded positions included.

    The packaged encoder needs its fast path off to produce a finite answer for
    a padded history at all — that defect is why it was replaced — so the
    reference runs with the switch off and the hand-written encoder needs nothing.
    """
    legacy = _perturbed_legacy(_config(), seed=seed).eval()
    hand_written = _converted(legacy)
    sequences = _left_padded(history_length, seed=seed)

    with torch.no_grad(), fastpath(False):
        expected = legacy.encode_positions(sequences)
    with torch.no_grad():
        actual = hand_written.encode_positions(sequences)

    assert torch.isfinite(actual).all()
    assert (actual - expected).abs().max().item() <= TOLERANCE


@pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
def test_train_mode_without_dropout_matches_outputs_and_gradients(history_length: int) -> None:
    """``train()`` mode at dropout 0: the same function, so the same loss surface.

    Dropout draws cannot match between two implementations, so this is the
    strongest training-time comparison available. Outputs are held to the brief's
    1e-5 in float32. Gradients are compared after converting the packaged
    encoder's gradients through the same converter, with both encoders in
    float64: a float32 gradient summed over a batch moves by an ulp-scale amount
    with the BLAS's reduction order (it did on CI's linux/amd64 runner, by
    1.5e-5), and double precision removes that noise so a real mismatch would show.
    """
    legacy = _perturbed_legacy(_config(dropout=0.0), seed=7).train()
    hand_written = _converted(legacy)
    sequences = _left_padded(history_length, seed=7)
    upstream = torch.randn(
        (sequences.shape[0], WINDOW, 64), generator=torch.Generator().manual_seed(3)
    )

    with torch.no_grad():
        expected = legacy.encode_positions(sequences)
        actual = hand_written.encode_positions(sequences)
    assert (actual - expected).abs().max().item() <= TOLERANCE

    legacy, hand_written = legacy.double(), hand_written.double()
    (legacy.encode_positions(sequences) * upstream.double()).sum().backward()  # type: ignore[no-untyped-call]
    (hand_written.encode_positions(sequences) * upstream.double()).sum().backward()  # type: ignore[no-untyped-call]
    legacy_gradients = legacy_state_to_hand_written(
        {
            name: (parameter.grad if parameter.grad is not None else torch.zeros_like(parameter))
            .detach()
            .numpy()
            for name, parameter in legacy.named_parameters()
        },
        num_blocks=legacy.config.num_blocks,
    )
    for name, parameter in hand_written.named_parameters():
        assert parameter.grad is not None, name
        torch.testing.assert_close(
            parameter.grad, torch.from_numpy(legacy_gradients[name]), rtol=1e-9, atol=1e-10
        )


@pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
def test_learned_weights_match_through_the_packaged_encoder(history_length: int) -> None:
    """Weights the trainer actually produced, run back through v1's encoder."""
    model = _trained_model()
    assert model._encoder is not None
    model._encoder.eval()
    legacy = legacy_encoder_from(model._encoder, model.config).eval()
    sequences = _left_padded(history_length, seed=11).clamp(max=len(model._index_to_item))

    with torch.no_grad(), fastpath(False):
        expected = legacy.encode_positions(sequences)
    with torch.no_grad():
        actual = model._encoder.encode_positions(sequences)

    assert (actual - expected).abs().max().item() <= TOLERANCE


def test_the_converter_round_trips_and_is_lossless() -> None:
    legacy = _perturbed_legacy(_config(), seed=5)
    arrays = legacy.state_arrays()
    converted = legacy_state_to_hand_written(arrays, num_blocks=2)
    back = hand_written_state_to_legacy(
        {name: torch.from_numpy(array) for name, array in converted.items()}, num_blocks=2
    )

    assert set(back) == set(arrays)
    for name, array in arrays.items():
        assert np.array_equal(back[name], array), name


def test_the_converter_refuses_anything_it_cannot_place() -> None:
    arrays = _perturbed_legacy(_config(), seed=5).state_arrays()

    with pytest.raises(ValueError, match="no hand-written counterpart"):
        legacy_state_to_hand_written(
            {**arrays, "transformer.norm.weight": np.ones(64)}, num_blocks=2
        )
    with pytest.raises(ValueError, match="beyond 1 blocks"):
        legacy_state_to_hand_written(arrays, num_blocks=1)
    missing = {k: v for k, v in arrays.items() if k != "transformer.layers.1.norm2.bias"}
    with pytest.raises(ValueError, match="block 1 is missing tensors: norm2.bias"):
        legacy_state_to_hand_written(missing, num_blocks=2)


# --- 2b. training parity (owner ruling, 2026-10-05) ----------------------------
#
# Inference equivalence does not prove that training behaves the same. These
# drive both encoders through the trainer of record's own step — WO-1's
# strict-prefix example store, the per-example negative sampler, BCE — from the
# same weights and the same generator state, and compare the loss and every
# gradient. Before the attention-output layout fix, dropout 0.2 failed here: the
# masks were the same random numbers laid on different elements.


def _training_loss(
    encoder: torch.nn.Module,
    histories: torch.Tensor,
    positives: torch.Tensor,
    negatives: torch.Tensor,
) -> torch.Tensor:
    """The loss of ``SASRecModel._train_strict_prefix``, statement for statement."""
    user_vectors = encoder.training_user_vectors(histories)  # type: ignore[operator]
    positive_logits = (
        user_vectors * encoder.item_vectors(positives, normalize=False)  # type: ignore[operator]
    ).sum(dim=1)
    negative_logits = torch.einsum(
        "bd,bkd->bk",
        user_vectors,
        encoder.item_vectors(negatives, normalize=False),  # type: ignore[operator]
    )
    return sampled_gbce_loss(positive_logits, negative_logits, beta=1.0)


def _gradients(encoder: torch.nn.Module, *, legacy: bool) -> dict[str, torch.Tensor]:
    """Every parameter's gradient under the hand-written names."""
    raw = {
        name: (parameter.grad if parameter.grad is not None else torch.zeros_like(parameter))
        .detach()
        .clone()
        for name, parameter in encoder.named_parameters()
    }
    if not legacy:
        return raw
    converted = legacy_state_to_hand_written(
        {name: tensor.numpy() for name, tensor in raw.items()},
        num_blocks=encoder.config.num_blocks,  # type: ignore[union-attr]
    )
    return {name: torch.from_numpy(array) for name, array in converted.items()}


def _hand_written_state(encoder: torch.nn.Module) -> dict[str, torch.Tensor]:
    converted = legacy_state_to_hand_written(
        {name: tensor.detach().numpy() for name, tensor in encoder.state_dict().items()},
        num_blocks=encoder.config.num_blocks,  # type: ignore[union-attr]
    )
    return {name: torch.from_numpy(array) for name, array in converted.items()}


def training_parity(
    examples: StrictPrefixExamples,
    *,
    n_items: int,
    config: SASRecConfig,
    dtype: torch.dtype,
    batches: int = 3,
) -> dict[str, Any]:
    """Run the first ``batches`` steps of the trainer of record through both encoders.

    Mirrors ``SASRecModel.fit``'s order of random draws: seed, encoder
    initialization, the epoch permutation, then the sampler's NumPy generator.
    The packaged encoder is built first and its weights converted into the
    hand-written one, so both start identical. Each batch is compared at those
    starting weights (loss, every gradient); then both take the same three Adam
    steps and the losses and weights are compared again.
    """
    torch.manual_seed(config.seed)
    legacy = LegacySASRecEncoder(n_items + 2, config)
    hand_written = SASRecEncoder(n_items + 2, config)
    hand_written.load_state_dict(_hand_written_state(legacy))
    legacy, hand_written = legacy.to(dtype).train(), hand_written.to(dtype).train()
    permutation = torch.randperm(len(examples))
    generator_state = torch.get_rng_state()
    rng = np.random.default_rng(config.seed)
    drawn = []
    for index in range(batches):
        rows = permutation[index * config.batch_size : (index + 1) * config.batch_size]
        histories, positives = examples.batch(rows)
        negatives = sample_negatives(
            histories, positives, n_items=n_items, count=config.negative_count, rng=rng
        )
        drawn.append((histories, positives, negatives))

    per_batch = []
    for histories, positives, negatives in drawn:
        legacy.zero_grad()
        hand_written.zero_grad()
        torch.set_rng_state(generator_state)
        legacy_loss = _training_loss(legacy, histories, positives, negatives)
        legacy_loss.backward()  # type: ignore[no-untyped-call]
        legacy_after = torch.get_rng_state()
        torch.set_rng_state(generator_state)
        hand_written_loss = _training_loss(hand_written, histories, positives, negatives)
        hand_written_loss.backward()  # type: ignore[no-untyped-call]
        expected = _gradients(legacy, legacy=True)
        actual = _gradients(hand_written, legacy=False)
        differences = {name: (actual[name] - expected[name]).abs().max().item() for name in actual}
        per_batch.append(
            {
                "rows": len(histories),
                "loss_packaged": legacy_loss.item(),
                "loss_hand_written": hand_written_loss.item(),
                "loss_abs_difference": abs(legacy_loss.item() - hand_written_loss.item()),
                "max_gradient_abs_difference": max(differences.values()),
                "worst_gradient_tensor": max(differences, key=differences.__getitem__),
                "max_gradient_abs": max(t.abs().max().item() for t in expected.values()),
                "rng_draws_identical": torch.equal(legacy_after, torch.get_rng_state()),
            }
        )

    trajectories = []
    for encoder in (legacy, hand_written):
        optimizer = torch.optim.Adam(encoder.parameters(), lr=config.learning_rate)
        torch.set_rng_state(generator_state)
        losses = []
        for histories, positives, negatives in drawn:
            optimizer.zero_grad()
            loss = _training_loss(encoder, histories, positives, negatives)
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            with torch.no_grad():
                encoder.item_embedding.weight[0].zero_()  # type: ignore[union-attr,index]
            losses.append(loss.item())
        trajectories.append(losses)
    after_legacy = _hand_written_state(legacy)
    after_hand_written = hand_written.state_dict()
    return {
        "dtype": str(dtype).removeprefix("torch."),
        "dropout": config.dropout,
        "batches": per_batch,
        "adam_losses_packaged": trajectories[0],
        "adam_losses_hand_written": trajectories[1],
        "adam_max_loss_abs_difference": max(
            abs(a - b) for a, b in zip(trajectories[0], trajectories[1], strict=True)
        ),
        "adam_max_weight_abs_difference_after_steps": max(
            (after_hand_written[name] - after_legacy[name]).abs().max().item()
            for name in after_hand_written
        ),
    }


def _synthetic_examples() -> tuple[StrictPrefixExamples, int]:
    rows = [
        (user, 1_000 + (user * 7 + step * 3) % 400, step)
        for user in range(1, 200)
        for step in range(60)
    ]
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    frame = frame.drop_duplicates(subset=["userId", "movieId"], keep="first")
    items = sorted(int(item) for item in frame["movieId"].unique())
    store = build_strict_prefix_example_store(
        frame, item_to_index={item: index + 1 for index, item in enumerate(items)}, max_length=50
    )
    return store, len(items)


# float32 is the training dtype and is held to the owner's 1e-5; float64 shows
# what is left once rounding is out of the way.
PARITY_TOLERANCE = {torch.float32: 1e-5, torch.float64: 1e-10}


@pytest.mark.parametrize("dtype", [torch.float32, torch.float64], ids=["float32", "float64"])
@pytest.mark.parametrize("dropout", [0.0, 0.2], ids=["dropout-0", "dropout-0.2"])
def test_same_seed_training_steps_match_the_packaged_encoder(
    dropout: float, dtype: torch.dtype
) -> None:
    store, n_items = _synthetic_examples()
    report = training_parity(
        store,
        n_items=n_items,
        config=SASRecConfig(dropout=dropout, negative_count=32, loss="bce", seed=42),
        dtype=dtype,
    )
    tolerance = PARITY_TOLERANCE[dtype]

    for batch in report["batches"]:
        assert batch["rng_draws_identical"]
        assert batch["loss_abs_difference"] <= tolerance, batch
        assert batch["max_gradient_abs_difference"] <= tolerance, batch
    assert report["adam_max_loss_abs_difference"] <= tolerance, report
    # Adam's first steps move each weight by about the learning rate whatever the
    # gradient's size, so a rounding-level gradient on a near-zero element can
    # move a weight by more than the gradients differ. Bounded well below 1e-3.
    assert report["adam_max_weight_abs_difference_after_steps"] <= 1e3 * tolerance, report


def test_dropout_sites_and_rates_match_the_packaged_encoder() -> None:
    """Four sites per block, in the same order, at the configured rate; none elsewhere."""
    config = _config(dropout=0.2, num_blocks=2)
    legacy = LegacySASRecEncoder(CATALOG, config)
    hand_written = SASRecEncoder(CATALOG, config)

    legacy_sites = []
    for layer in legacy.transformer.layers:
        legacy_sites += [
            ("attention weights", layer.self_attn.dropout),
            ("attention output, before the residual add", layer.dropout1.p),
            ("feed-forward, after GELU", layer.dropout.p),
            ("feed-forward output, before the residual add", layer.dropout2.p),
        ]
    hand_written_sites = []
    for block in hand_written.transformer.blocks:
        hand_written_sites += [
            ("attention weights", block.attention.weight_dropout.p),
            ("attention output, before the residual add", block.attention_dropout.p),
            ("feed-forward, after GELU", block.feed_forward.dropout.p),
            ("feed-forward output, before the residual add", block.feed_forward_dropout.p),
        ]
    assert hand_written_sites == legacy_sites
    assert all(rate == 0.2 for _site, rate in hand_written_sites)
    # No embedding dropout or any other site, in either encoder.
    dropouts = [
        name
        for name, module in hand_written.named_modules()
        if isinstance(module, torch.nn.Dropout)
    ]
    assert len(dropouts) == 4 * config.num_blocks
    legacy_dropouts = [
        name for name, module in legacy.named_modules() if isinstance(module, torch.nn.Dropout)
    ]
    assert len(legacy_dropouts) == 3 * config.num_blocks  # plus the float rate inside attention


# --- 3. saved models keep loading ---------------------------------------------


def test_a_pre_wo2_archive_loads_and_retrieves_what_the_old_encoder_retrieved(
    tmp_path: Path,
) -> None:
    model = _trained_model()
    assert model._encoder is not None
    model.build_index()
    export_sasrec(model, tmp_path)
    rewrite_as_legacy_artifact(tmp_path)
    manifest = SASRecArtifactManifest.load(tmp_path / MANIFEST_FILENAME)
    assert manifest.encoder_impl == ENCODER_IMPL_LEGACY
    assert "encoder_impl" not in json.loads((tmp_path / MANIFEST_FILENAME).read_text())

    loaded = load_sasrec(tmp_path / MANIFEST_FILENAME)
    old = SASRecModel(config=model.config, cold_start_threshold=None)
    old._item_to_index, old._index_to_item = model._item_to_index, model._index_to_item
    old._unknown_index = model._unknown_index
    old._encoder = legacy_encoder_from(model._encoder, model.config).eval()  # type: ignore[assignment]
    old.build_index()

    movie_ids = sorted(model._item_to_index)
    for length in HISTORY_LENGTHS:
        history = movie_ids[:length]
        with fastpath(False):
            expected = old.recommend_from_history(history, 100)
        assert loaded.recommend_from_history(history, 100) == expected
        assert loaded.recommend_from_history(history, 100) == model.recommend_from_history(
            history, 100
        )


def test_a_new_export_records_the_hand_written_layout(tmp_path: Path) -> None:
    manifest = export_sasrec(_trained_model(), tmp_path)

    assert manifest.encoder_impl == ENCODER_IMPL_HAND_WRITTEN
    assert SASRecArtifactManifest.load(tmp_path / MANIFEST_FILENAME) == manifest


def test_an_unknown_layout_is_refused(tmp_path: Path) -> None:
    export_sasrec(_trained_model(), tmp_path)
    path = tmp_path / MANIFEST_FILENAME
    raw = json.loads(path.read_text())
    raw["encoder_impl"] = "something-else"
    path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="unsupported SASRec encoder layout"):
        load_sasrec(path)


def test_metadata_and_manifest_must_agree_on_the_layout(tmp_path: Path) -> None:
    export_sasrec(_trained_model(), tmp_path)
    path = tmp_path / MANIFEST_FILENAME
    raw = json.loads(path.read_text())
    raw["encoder_impl"] = ENCODER_IMPL_LEGACY
    path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="encoder layout does not match"):
        load_sasrec(path)


# --- 4. behaviour ---------------------------------------------------------------


def test_memorization_recovers_a_deterministic_next_movie() -> None:
    """Every user walks the same 40-movie cycle from a different start.

    The next movie is a pure function of the last one, so a working sequence
    model must put it in its top 10 for every user. A broken mask, a broken
    residual or a broken gradient path all leave recall far from 1.
    """
    catalog = 40
    rows: list[tuple[int, int, int]] = []
    targets: dict[int, set[int]] = {}
    for user in range(1, 81):
        start = (user * 3) % catalog
        rows.extend((user, 100 + (start + step) % catalog, step) for step in range(8))
        targets[user] = {100 + (start + 8) % catalog}
    config = _config(
        max_sequence_length=10,
        hidden_dim=32,
        feedforward_dim=64,
        dropout=0.0,
        negative_count=8,
        batch_size=64,
        epochs=60,
        loss="bce",
        faiss_exact=True,
        seed=42,
    )
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"]),
        retrieval_backend="torch",
    )

    recall = np.mean(
        [recall_at_k(target, model.recommend(user, 10), 10) for user, target in targets.items()]
    )

    assert recall >= 0.95


def test_every_weight_receives_a_gradient() -> None:
    torch.manual_seed(0)
    encoder = SASRecEncoder(CATALOG, _config(dropout=0.0)).train()
    sequences = torch.cat([_left_padded(length, rows=2) for length in HISTORY_LENGTHS])

    encoder.encode_positions(sequences).pow(2).sum().backward()  # type: ignore[no-untyped-call]

    for name, parameter in encoder.named_parameters():
        assert parameter.grad is not None, name
        assert torch.isfinite(parameter.grad).all(), name
        if name == "item_embedding.weight":
            # Row 0 is the padding id and must stay untrainable; every other row
            # that appeared in the batch must move.
            assert torch.count_nonzero(parameter.grad[0]) == 0
            assert torch.count_nonzero(parameter.grad[1:]) > 0
        else:
            assert torch.count_nonzero(parameter.grad) > 0, name


@pytest.mark.parametrize("mode", ["eval", "train-without-dropout"])
@pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
def test_a_later_movie_cannot_change_an_earlier_output(mode: str, history_length: int) -> None:
    """Change the newest movie and every earlier position must be bit-identical."""
    torch.manual_seed(1)
    encoder = SASRecEncoder(CATALOG, _config(dropout=0.0)).train(mode != "eval")
    original = _left_padded(history_length, rows=2)
    changed = original.clone()
    changed[:, -1] = (original[:, -1] % (CATALOG - 1)) + 1

    with torch.no_grad():
        before = encoder.encode_positions(original)
        after = encoder.encode_positions(changed)

    assert torch.equal(before[:, :-1], after[:, :-1])
    assert not torch.equal(before[:, -1], after[:, -1])


@pytest.mark.parametrize("history_length", HISTORY_LENGTHS)
def test_padded_positions_are_exact_zeros_and_never_nan(history_length: int) -> None:
    """Inside the stack as well as at its output, in train mode with dropout on.

    Each block's own output is finite everywhere — padded rows included, where
    attention had nothing to read — and what the stack hands on and what the
    encoder returns are exact zeros at every padded position.
    """
    torch.manual_seed(2)
    encoder = SASRecEncoder(CATALOG, _config(dropout=0.5)).train()
    sequences = _left_padded(history_length)
    padding = sequences.eq(0)
    block_outputs: list[torch.Tensor] = []
    stack_outputs: list[torch.Tensor] = []
    handles = [
        block.register_forward_hook(lambda _m, _i, output: block_outputs.append(output))
        for block in encoder.transformer.blocks
    ]
    handles.append(
        encoder.transformer.register_forward_hook(
            lambda _m, _i, output: stack_outputs.append(output)
        )
    )
    try:
        encoded = encoder.encode_positions(sequences)
    finally:
        for handle in handles:
            handle.remove()

    assert len(block_outputs) == 2 and len(stack_outputs) == 1
    for output in block_outputs:
        assert torch.isfinite(output).all()
    for output in (stack_outputs[0], encoded):
        assert torch.isfinite(output).all()
        assert torch.count_nonzero(output[padding]) == 0
    assert torch.count_nonzero(encoded[~padding]) > 0


def test_attention_on_a_row_with_nothing_to_read_is_zero_not_nan() -> None:
    """The case that broke the old encoder, at the function that now owns it."""
    padding = torch.ones((1, 4), dtype=torch.bool)
    query = torch.randn(1, 2, 4, 8, requires_grad=True)
    key = torch.randn(1, 2, 4, 8, requires_grad=True)
    value = torch.randn(1, 2, 4, 8, requires_grad=True)

    attended = scaled_dot_product(query, key, value, attention_mask(padding), torch.nn.Dropout(0.0))
    attended.sum().backward()  # type: ignore[no-untyped-call]

    assert torch.count_nonzero(attended) == 0
    for tensor in (query, key, value):
        assert tensor.grad is not None and torch.isfinite(tensor.grad).all()


def test_an_entirely_padded_sequence_encodes_to_zeros_with_finite_gradients() -> None:
    torch.manual_seed(3)
    encoder = SASRecEncoder(CATALOG, _config()).train()
    sequences = torch.cat([torch.zeros((1, WINDOW), dtype=torch.long), _left_padded(5, rows=1)])

    encoded = encoder.encode_positions(sequences)
    encoded.sum().backward()  # type: ignore[no-untyped-call]

    assert torch.count_nonzero(encoded[0]) == 0
    for name, parameter in encoder.named_parameters():
        assert parameter.grad is None or torch.isfinite(parameter.grad).all(), name


# --- the pinned full-data artifact (local only) -----------------------------------
#
# CI has no run-scoped archive, so these skip there. With the archive present:
#
#   SASREC_PINNED_MANIFEST=artifacts/sasrec/a11af5ed0f0745f68572407237cfa4b9/sasrec-manifest.json \
#     OMP_NUM_THREADS=1 pytest tests/unit/test_sasrec_transformer.py -k pinned
#
# and, for the population-level list comparison (loads the full split, minutes):
#
#   SASREC_V1_LIST_DIFF_OUT=artifacts/sasrec/wo2-converter/list-diff.json ... (same command)


def _pinned_manifest() -> Path:
    raw = os.environ.get("SASREC_PINNED_MANIFEST", "").strip()
    if not raw:
        pytest.skip("SASREC_PINNED_MANIFEST is not available")
    return Path(raw)


def test_pinned_v1_artifact_matches_the_packaged_encoder_on_its_own_weights() -> None:
    manifest_path = _pinned_manifest()
    manifest = SASRecArtifactManifest.load(manifest_path)
    assert manifest.encoder_impl == ENCODER_IMPL_LEGACY
    loaded = load_sasrec(manifest_path)
    assert loaded._encoder is not None
    old = legacy_encoder_from(loaded._encoder, loaded.config).eval()

    for length in HISTORY_LENGTHS:
        sequences = _left_padded(length, rows=16).clamp(max=len(loaded._index_to_item))
        with torch.no_grad(), fastpath(False):
            expected = old.encode_positions(sequences)
        with torch.no_grad():
            actual = loaded._encoder.encode_positions(sequences)
        assert (actual - expected).abs().max().item() <= TOLERANCE, length


def test_pinned_v1_population_lists_against_the_packaged_encoder() -> None:
    """WO-2 done-criterion 3's list count: same weights, same users, both encoders.

    Recall is scored through ``src.evaluation.protocol.evaluate`` for both, and the
    result written to ``SASREC_V1_LIST_DIFF_OUT``. The formal MLflow record of the
    reproduction is ``SASREC_RECHECK_SCOPE=converter python -m
    src.training.sasrec_fastpath_recheck``; this test answers the question that
    run cannot, because the packaged encoder may not be imported under ``src/``.
    """
    out = os.environ.get("SASREC_V1_LIST_DIFF_OUT", "").strip()
    manifest_path = _pinned_manifest()
    if not out:
        pytest.skip("SASREC_V1_LIST_DIFF_OUT is not set")
    os.environ.setdefault("SASREC_RANKER_ARTIFACT_DIR", str(manifest_path.parent))

    from src.evaluation.protocol import K_CANDIDATES, evaluate
    from src.training.sasrec_fastpath_recheck import (
        count_changed_lists,
        prepare_retrieval_inputs,
        reproduction_verdict,
    )

    inputs = prepare_retrieval_inputs()
    model = inputs.sasrec.model
    holdout = inputs.split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    train_counts = inputs.split.train.groupby("userId").size().to_dict()
    user_ids = list(holdout)

    new_lists = model.recommend_for_users(user_ids, K_CANDIDATES)
    assert model._encoder is not None
    hand_written = model._encoder
    model._encoder = legacy_encoder_from(hand_written, model.config).eval()  # type: ignore[assignment]
    try:
        with fastpath(False):
            old_lists = model.recommend_for_users(user_ids, K_CANDIDATES)
    finally:
        model._encoder = hand_written

    new = evaluate(new_lists, holdout, train_counts, k=K_CANDIDATES)
    old = evaluate(old_lists, holdout, train_counts, k=K_CANDIDATES)
    document = {
        "hand_written": reproduction_verdict(new.warm.recall, new.cold.recall),
        "packaged_encoder_same_weights": {
            "warm_recall": old.warm.recall,
            "cold_recall": old.cold.recall,
        },
        "warm_recall_difference_hand_written_minus_packaged": new.warm.recall - old.warm.recall,
        "lists": count_changed_lists(old_lists, new_lists),
        "warm_lists": count_changed_lists(
            {u: old_lists[u] for u in user_ids if model.was_served_by_sasrec(u)},
            {u: new_lists[u] for u in user_ids if model.was_served_by_sasrec(u)},
        ),
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps(document, indent=2, sort_keys=True))

    assert document["hand_written"]["passed"], document


# Initialization scheme per tensor, in the order the random generator is drawn.
# "none" draws nothing. The packaged encoder's scheme is PyTorch's defaults for
# the classes it used; the hand-written one replays them (transformer.py).
INIT_SCHEMES = [
    ("item_embedding.weight", "normal(0, 1), redrawn last as normal(0, 1/sqrt(d)); row 0 zeroed"),
    ("position_embedding.weight", "normal(0, 1)"),
    ("transformer.blocks.0.attention.output.weight", "kaiming_uniform(a=sqrt(5)): U(+-1/sqrt(d))"),
    ("transformer.blocks.0.attention.output.bias", "U(+-1/sqrt(d)) drawn, then zeroed"),
    (
        "transformer.blocks.0.attention.{query,key,value}.weight",
        "one xavier_uniform (3d, d) draw, rows sliced: U(+-sqrt(6/(d+3d)))",
    ),
    ("transformer.blocks.0.attention.{query,key,value}.bias", "zeros (no draw)"),
    (
        "transformer.blocks.0.feed_forward.expand.weight",
        "kaiming_uniform(a=sqrt(5)): U(+-1/sqrt(d))",
    ),
    ("transformer.blocks.0.feed_forward.expand.bias", "U(+-1/sqrt(d))"),
    (
        "transformer.blocks.0.feed_forward.contract.weight",
        "kaiming_uniform(a=sqrt(5)): U(+-1/sqrt(F))",
    ),
    ("transformer.blocks.0.feed_forward.contract.bias", "U(+-1/sqrt(F))"),
    ("transformer.blocks.*.{attention,feed_forward}_norm, output_norm", "ones / zeros (no draw)"),
    ("transformer.blocks.1..N-1.*", "deep copies of block 0 (no draw)"),
]


def test_training_parity_on_three_real_pilot_batches() -> None:
    """The owner's ruling, on the pilot's own data: WO-1's first three batches at seed 42.

    The partition is built exactly as ``run_once`` builds it for the 6% pilot
    under O-25, so these are the batches the seed-42 pilot trained on first.
    Writes a JSON report to ``SASREC_WO2_TRAINING_PARITY_OUT``; skipped without it.
    """
    out = os.environ.get("SASREC_WO2_TRAINING_PARITY_OUT", "").strip()
    input_dir = os.environ.get("TWOTOWER_INPUT_DIR", "").strip()
    if not out or not input_dir:
        pytest.skip("SASREC_WO2_TRAINING_PARITY_OUT and TWOTOWER_INPUT_DIR are not both set")

    from src.config import Settings
    from src.data.split import temporal_cutoff, temporal_split
    from src.training.candidate_data import load_inputs, subsample_users
    from src.training.sasrec import SUBSAMPLE_SEED

    full, _movies = load_inputs(Settings(), input_dir=Path(input_dir))
    sample = subsample_users(full, 0.06, SUBSAMPLE_SEED)
    split = temporal_split(sample, cutoff=temporal_cutoff(full))
    assert int(split.train["timestamp"].max()) < split.cutoff < split.holdout_end == 1469256597
    items = sorted(int(item) for item in split.train["movieId"].unique())
    store = build_strict_prefix_example_store(
        split.train,
        item_to_index={item: index + 1 for index, item in enumerate(items)},
        max_length=50,
    )
    cell = SASRecConfig(loss="bce", negative_count=32, epochs=2, faiss_exact=True, seed=42)

    torch.manual_seed(cell.seed)
    legacy = LegacySASRecEncoder(len(items) + 2, cell)
    legacy_rng = torch.get_rng_state()
    torch.manual_seed(cell.seed)
    hand_written = SASRecEncoder(len(items) + 2, cell)
    same_draws = torch.equal(torch.get_rng_state(), legacy_rng)
    converted = _hand_written_state(legacy)
    initialization = [
        {
            "tensor": name,
            "shape": list(tensor.shape),
            "mean": tensor.mean().item(),
            "std": tensor.std().item() if tensor.numel() > 1 else 0.0,
            "min": tensor.min().item(),
            "max": tensor.max().item(),
            "bit_identical_to_packaged": torch.equal(tensor, converted[name]),
        }
        for name, tensor in hand_written.state_dict().items()
    ]

    reports = [
        training_parity(
            store,
            n_items=len(items),
            config=SASRecConfig(**{**cell.as_params(), "dropout": dropout}),
            dtype=dtype,
        )
        for dropout in (0.0, cell.dropout)
        for dtype in (torch.float32, torch.float64)
    ]
    document = {
        "partition": {
            "sample_fraction": 0.06,
            "subsample_seed": SUBSAMPLE_SEED,
            "cutoff": split.cutoff,
            "holdout_end": split.holdout_end,
            "train_rows": len(split.train),
            "latest_fit_timestamp": int(split.train["timestamp"].max()),
            "n_items": len(items),
            "n_examples": len(store),
        },
        "initialization": {
            "schemes_in_draw_order": [list(row) for row in INIT_SCHEMES],
            "generator_state_identical_after_construction": same_draws,
            "per_tensor": initialization,
        },
        "parity": reports,
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    assert document["initialization"]["generator_state_identical_after_construction"]
    assert all(row["bit_identical_to_packaged"] for row in initialization)
    for report in reports:
        tolerance = PARITY_TOLERANCE[
            torch.float32 if report["dtype"] == "float32" else torch.float64
        ]
        for batch in report["batches"]:
            assert batch["rng_draws_identical"]
            assert batch["loss_abs_difference"] <= tolerance, batch
            assert batch["max_gradient_abs_difference"] <= tolerance, batch
