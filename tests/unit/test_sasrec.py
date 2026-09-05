"""Correctness tests for the ADR 0016 sequential retriever."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import torch

from src.models.candidates.sasrec import (
    SASRecConfig,
    SASRecEncoder,
    SASRecModel,
    gbce_beta,
    sample_negatives,
    sampled_gbce_loss,
)


def _train() -> pd.DataFrame:
    return pd.DataFrame(
        [(user, 100 + user + item, item) for user in range(1, 5) for item in range(6)],
        columns=["userId", "movieId", "timestamp"],
    )


def _config(**changes: object) -> SASRecConfig:
    values = {
        "max_sequence_length": 5,
        "hidden_dim": 8,
        "num_blocks": 1,
        "num_heads": 2,
        "feedforward_dim": 16,
        "dropout": 0.0,
        "negative_count": 2,
        "batch_size": 8,
        "epochs": 1,
        "faiss_exact": True,
        "seed": 42,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def test_causal_mask_prevents_future_token_influence() -> None:
    torch.manual_seed(1)
    encoder = SASRecEncoder(10, _config()).eval()
    first = torch.tensor([[0, 1, 2, 3, 4]])
    changed_future = torch.tensor([[0, 1, 2, 3, 9]])
    with torch.no_grad():
        first_positions = encoder.encode_positions(first)
        changed_positions = encoder.encode_positions(changed_future)
    assert torch.equal(first_positions[:, :4], changed_positions[:, :4])


def test_training_vectors_are_not_retrieval_normalized() -> None:
    encoder = SASRecEncoder(10, _config()).eval()
    sequence = torch.tensor([[0, 1, 2, 3, 4]])
    with torch.no_grad():
        training = encoder.training_user_vectors(sequence)
        retrieval = encoder(sequence)
    assert not torch.allclose(torch.linalg.vector_norm(training, dim=1), torch.ones(1))
    assert torch.allclose(torch.linalg.vector_norm(retrieval, dim=1), torch.ones(1))


def _two_block_retrieval_model() -> SASRecModel:
    config = _config(
        max_sequence_length=50,
        num_blocks=2,
        dropout=0.0,
        faiss_exact=True,
    )
    movie_ids = list(range(1_000, 1_600))
    model = SASRecModel(config=config, cold_start_threshold=None)
    model._item_to_index = {movie_id: index + 1 for index, movie_id in enumerate(movie_ids)}
    model._index_to_item = {index: movie_id for movie_id, index in model._item_to_index.items()}
    model._unknown_index = len(movie_ids) + 1
    model._encoder = SASRecEncoder(len(movie_ids) + 2, config)
    model.build_index()
    return model


def test_two_block_eval_retrieves_500_for_every_supported_history_length() -> None:
    model = _two_block_retrieval_model()
    movie_ids = sorted(model._item_to_index)

    for length in (1, 3, 12, 49, 50):
        history = movie_ids[:length]
        encoded = model.encode_movie_history(history)
        scored = model.recommend_from_history_scored(history, 500)

        assert torch.isfinite(encoded).all(), f"non-finite query at history length {length}"
        assert len(scored) == 500, f"short slate at history length {length}"
        assert all(np.isfinite(score) for _movie_id, score in scored)
        assert model.recommend_from_history(history, 500) == [movie_id for movie_id, _ in scored]


def test_safe_attention_path_preserves_full_length_output() -> None:
    torch.manual_seed(11)
    config = _config(max_sequence_length=50, num_blocks=2, dropout=0.0)
    encoder = SASRecEncoder(64, config).eval()
    sequence = torch.arange(1, 51).unsqueeze(0)
    positions = torch.arange(50).unsqueeze(0)
    values = encoder.item_embedding(sequence) + encoder.position_embedding(positions)
    causal_mask = torch.triu(torch.ones(50, 50, dtype=torch.bool), diagonal=1)

    try:
        torch.backends.mha.set_fastpath_enabled(True)
        with torch.no_grad():
            fast = encoder.output_norm(
                encoder.transformer(
                    values,
                    mask=causal_mask,
                    src_key_padding_mask=sequence.eq(0),
                )
            )
    finally:
        torch.backends.mha.set_fastpath_enabled(False)
    with torch.no_grad():
        safe = encoder.encode_positions(sequence)

    assert torch.max(torch.abs(fast - safe)).item() <= 1e-6


def test_sampled_negatives_exclude_prefix_target_and_duplicates() -> None:
    histories = torch.tensor([[0, 1, 2], [2, 3, 4]])
    positives = torch.tensor([3, 5])
    negatives = sample_negatives(
        histories,
        positives,
        n_items=8,
        count=3,
        rng=np.random.default_rng(42),
    )
    for history, positive, sampled in zip(histories, positives, negatives):
        assert not (set(sampled.tolist()) & set(history.tolist()))
        assert positive.item() not in sampled.tolist()
        assert len(set(sampled.tolist())) == len(sampled)


def test_gbce_endpoints_and_bce_equivalence() -> None:
    assert gbce_beta(negative_count=2, catalog_size=11, calibration_t=0.0) == 1.0
    assert gbce_beta(negative_count=2, catalog_size=11, calibration_t=1.0) == pytest.approx(0.2)
    positive = torch.tensor([0.2, -0.4])
    negative = torch.tensor([[0.1, -0.3], [0.8, -1.0]])
    expected = (
        torch.nn.functional.binary_cross_entropy_with_logits(
            positive, torch.ones_like(positive), reduction="none"
        )
        + torch.nn.functional.binary_cross_entropy_with_logits(
            negative, torch.zeros_like(negative), reduction="none"
        ).sum(dim=1)
    ).mean() / 3
    assert torch.allclose(sampled_gbce_loss(positive, negative, beta=1.0), expected)


def test_config_reads_environment_and_serializes() -> None:
    config = SASRecConfig.from_env(
        {
            "SASREC_HIDDEN_DIM": "32",
            "SASREC_LOSS": "bce",
            "SASREC_CALIBRATION_T": "0.25",
            "SASREC_FAISS_EXACT": "true",
        }
    )
    assert config.hidden_dim == 32
    assert config.loss == "bce"
    assert config.calibration_t == 0.25
    assert config.faiss_exact is True
    assert config.as_params()["hidden_dim"] == 32


def test_fit_is_deterministic_and_recommendations_exclude_history() -> None:
    first = SASRecModel(config=_config(), cold_start_threshold=None).fit(_train())
    second = SASRecModel(config=_config(), cold_start_threshold=None).fit(_train())
    assert first._encoder is not None and second._encoder is not None
    for name, value in first._encoder.state_dict().items():
        assert torch.equal(value, second._encoder.state_dict()[name])
    recommendations = first.recommend(1, 3)
    seen = set(_train().loc[_train()["userId"] == 1, "movieId"])
    assert not (set(recommendations) & seen)
    assert recommendations == second.recommend(1, 3)


def test_epoch_evaluation_matches_final_inference_with_dropout() -> None:
    epoch_recommendations: list[list[int]] = []
    model = SASRecModel(config=_config(dropout=0.5), cold_start_threshold=None)

    def capture_epoch(_epoch: int, _loss: float) -> None:
        model.build_index()
        epoch_recommendations.append(model.recommend(1, 3))

    model.fit(_train(), on_epoch=capture_epoch)

    assert model._encoder is not None
    assert model._encoder.training is False
    assert epoch_recommendations == [model.recommend(1, 3)]


@pytest.mark.parametrize(
    "config",
    [_config(hidden_dim=7), _config(negative_count=0), _config(calibration_t=1.1)],
)
def test_invalid_configuration_is_rejected(config: SASRecConfig) -> None:
    with pytest.raises(ValueError):
        SASRecModel(config=config).fit(_train())
