"""WO-1: the restored trainer of record, on the memory-bounded data path.

The reference below is ``SASRecModel.fit`` from ``git show
89520be^:src/models/candidates/sasrec.py`` — the loop behind run 528b1451 —
with only its index build removed. It still materializes the copied
``(n_examples, max_length)`` history table. The restored loop must produce the
same weights from it bit for bit; that is the in-repo version of the held
6% pilot comparison, small enough to run on every commit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

import src.models.candidates.sequence_data as sequence_data
from src.data.split import temporal_cutoff, temporal_split
from src.models.candidates.sasrec import (
    ALL_POSITION_TRAINING_OBJECTIVE,
    LEGACY_TRAINING_OBJECTIVE,
    SASRecConfig,
    SASRecEncoder,
    SASRecModel,
    gbce_beta,
    sample_negatives,
    sampled_gbce_loss,
)
from src.models.candidates.sasrec_artifact import (
    MODEL_FILENAME,
    _read_model_archive,
    export_sasrec,
)
from src.models.candidates.sequence_data import (
    StrictPrefixExamples,
    build_strict_prefix_examples_with_stats,
)
from src.training.sasrec import (
    SealedPartitionError,
    _configuration_id,
    encoder_weights_sha256,
    peak_rss_bytes,
    sealed_partition_params,
)


def _config(**changes: object) -> SASRecConfig:
    values: dict[str, object] = {
        "max_sequence_length": 6,
        "hidden_dim": 8,
        "num_blocks": 2,
        "num_heads": 2,
        "feedforward_dim": 16,
        # Dropout on, so the comparison also covers the torch RNG stream that
        # dropout consumes inside every forward pass.
        "dropout": 0.2,
        "negative_count": 3,
        "loss": "bce",
        "batch_size": 16,
        "epochs": 2,
        "faiss_exact": True,
        "seed": 42,
    }
    values.update(changes)
    return SASRecConfig(**values)  # type: ignore[arg-type]


def _train(seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[tuple[int, int, int]] = []
    for user in range(1, 31):
        length = int(rng.integers(2, 25))
        watched = rng.choice(np.arange(100, 160), size=length, replace=False)
        clock = np.sort(rng.integers(0, max(2, length - 3), size=length))
        rows.extend((user, int(item), int(t)) for item, t in zip(watched, clock))
    frame = pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])
    return frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def _trainer_of_record(
    train: pd.DataFrame, config: SASRecConfig
) -> tuple[dict[str, torch.Tensor], list[float]]:
    """``SASRecModel.fit`` at 89520be^, statement for statement, index build omitted."""
    torch.manual_seed(config.seed)
    np.random.seed(config.seed)
    items = sorted(int(item) for item in train["movieId"].unique())
    item_to_index = {item: index + 1 for index, item in enumerate(items)}
    histories, positives, _stats = build_strict_prefix_examples_with_stats(
        train,
        item_to_index=item_to_index,
        max_length=config.max_sequence_length,
    )
    encoder = SASRecEncoder(len(items) + 2, config)
    optimizer = torch.optim.Adam(encoder.parameters(), lr=config.learning_rate)
    rng = np.random.default_rng(config.seed)
    beta = (
        1.0
        if config.loss == "bce"
        else gbce_beta(
            negative_count=config.negative_count,
            catalog_size=len(items),
            calibration_t=config.calibration_t,
        )
    )
    epoch_losses: list[float] = []
    for _epoch in range(config.epochs):
        encoder.train()
        permutation = torch.randperm(len(positives))
        epoch_loss = 0.0
        batches = 0
        for start in range(0, len(positives), config.batch_size):
            rows = permutation[start : start + config.batch_size]
            history_batch = histories[rows].long()
            positive_batch = positives[rows].long()
            negative_batch = sample_negatives(
                history_batch,
                positive_batch,
                n_items=len(items),
                count=config.negative_count,
                rng=rng,
            )
            user_vectors = encoder.training_user_vectors(history_batch)
            positive_logits = (
                user_vectors * encoder.item_vectors(positive_batch, normalize=False)
            ).sum(dim=1)
            negative_logits = torch.einsum(
                "bd,bkd->bk",
                user_vectors,
                encoder.item_vectors(negative_batch, normalize=False),
            )
            loss = sampled_gbce_loss(positive_logits, negative_logits, beta=beta)
            optimizer.zero_grad()
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            with torch.no_grad():
                encoder.item_embedding.weight[0].zero_()
            epoch_loss += float(loss.item())
            batches += 1
        epoch_losses.append(epoch_loss / max(1, batches))
    return encoder.state_dict(), epoch_losses


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"loss": "gbce", "calibration_t": 0.5, "seed": 7},
        {"max_sequence_length": 50, "batch_size": 5, "seed": 13},
    ],
)
def test_restored_loop_reproduces_the_trainer_of_record_bit_for_bit(
    changes: dict[str, object],
) -> None:
    config = _config(**changes)
    train = _train()
    expected_state, expected_losses = _trainer_of_record(train, config)

    losses: list[float] = []
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        train,
        on_epoch=lambda _epoch, loss: losses.append(loss),
        retrieval_backend="torch",
    )

    assert model._encoder is not None
    state = model._encoder.state_dict()
    assert sorted(state) == sorted(expected_state)
    for name, value in expected_state.items():
        assert torch.equal(state[name], value), name
    assert losses == expected_losses
    assert encoder_weights_sha256(state) == encoder_weights_sha256(expected_state)
    assert model._training_objective == LEGACY_TRAINING_OBJECTIVE


def test_restored_loop_never_builds_the_copied_history_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No examples x window array: the copying builder is never called, batches stay batches."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the restored loop must not materialize copied prefixes")

    monkeypatch.setattr(sequence_data, "build_strict_prefix_examples_with_stats", refuse)
    monkeypatch.setattr(sequence_data, "build_strict_prefix_examples", refuse)
    batch_shapes: list[tuple[int, ...]] = []
    original_batch = StrictPrefixExamples.batch

    def recording_batch(
        self: StrictPrefixExamples, rows: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        histories, positives = original_batch(self, rows)
        batch_shapes.append(tuple(histories.shape))
        return histories, positives

    monkeypatch.setattr(StrictPrefixExamples, "batch", recording_batch)
    config = _config(batch_size=16, max_sequence_length=50)
    model = SASRecModel(config=config, cold_start_threshold=None).fit(
        _train(), retrieval_backend="torch"
    )

    assert model._training_stats is not None
    n_examples = model._training_stats.n_targets
    assert batch_shapes
    assert all(rows <= config.batch_size for rows, _length in batch_shapes)
    assert {length for _rows, length in batch_shapes} == {config.max_sequence_length}
    assert sum(rows for rows, _length in batch_shapes) == config.epochs * n_examples
    copied_table_bytes = n_examples * config.max_sequence_length * 4
    assert 0 < model._training_example_bytes < copied_table_bytes


def test_objective_is_a_validated_config_field_read_from_the_environment() -> None:
    assert SASRecConfig().training_objective == LEGACY_TRAINING_OBJECTIVE
    from_env = SASRecConfig.from_env({"SASREC_TRAINING_OBJECTIVE": ALL_POSITION_TRAINING_OBJECTIVE})
    assert from_env.training_objective == ALL_POSITION_TRAINING_OBJECTIVE
    with pytest.raises(ValueError, match="training_objective"):
        SASRecModel(config=_config(training_objective="final-position-v2")).fit(_train())


def test_configuration_id_keeps_the_record_id_and_separates_the_ablation() -> None:
    record = _configuration_id(_config())
    ablation = _configuration_id(_config(training_objective=ALL_POSITION_TRAINING_OBJECTIVE))
    assert record != ablation
    # The objective of record's ids predate the field, so restoring the loop
    # must not mint a new id for the same experiment.
    assert record == _configuration_id(_config(training_objective=LEGACY_TRAINING_OBJECTIVE))


def test_weights_digest_reads_the_same_from_a_model_and_its_archive(tmp_path: Path) -> None:
    model = SASRecModel(config=_config(epochs=1), cold_start_threshold=None).fit(
        _train(), retrieval_backend="torch"
    )
    assert model._encoder is not None
    export_sasrec(model, tmp_path / "run")
    _metadata, arrays = _read_model_archive(tmp_path / "run" / MODEL_FILENAME)

    assert encoder_weights_sha256(arrays) == encoder_weights_sha256(model._encoder.state_dict())
    other = SASRecModel(config=_config(epochs=1, seed=7), cold_start_threshold=None).fit(
        _train(), retrieval_backend="torch"
    )
    assert other._encoder is not None
    assert encoder_weights_sha256(other._encoder.state_dict()) != encoder_weights_sha256(arrays)


def _ratings_over_days(days: int = 400) -> pd.DataFrame:
    rows = [
        (user, 1_000 + (user * 13 + day) % 97, day * 86_400 + user)
        for user in range(1, 41)
        for day in range(0, days, 1 + user % 5)
    ]
    return pd.DataFrame(rows, columns=["userId", "movieId", "timestamp"])


def test_partition_guard_records_a_run_inside_the_boundary() -> None:
    ratings = _ratings_over_days()
    split = temporal_split(ratings)

    params = sealed_partition_params(ratings, split, split.train)

    assert params["sealed_boundary_timestamp"] == split.holdout_end
    assert params["holdout_end_timestamp"] == split.holdout_end
    assert params["latest_fit_timestamp"] < split.cutoff
    assert params["latest_scored_timestamp"] < split.holdout_end


def test_partition_guard_refuses_a_subsample_whose_holdout_reaches_the_sealed_window() -> None:
    """A subsample computes its own cutoff; its holdout must still stop at the full boundary."""
    ratings = _ratings_over_days()
    boundary = temporal_split(ratings).holdout_end
    # Users whose activity is concentrated late push the subsample's
    # 80th-percentile cutoff, and with it the 28-day holdout, past the boundary.
    late = ratings[ratings["timestamp"] >= boundary - 40 * 86_400]
    split = temporal_split(late)
    assert split.holdout["timestamp"].max() >= boundary

    with pytest.raises(SealedPartitionError, match="sealed partition"):
        sealed_partition_params(ratings, split, split.train)


def test_peak_rss_is_reported_in_bytes() -> None:
    # Any live Python process with torch loaded is far above 10 MB and below 1 TB;
    # a KiB/byte mix-up lands outside that range on either platform.
    assert 10 * 2**20 < peak_rss_bytes() < 2**40


def test_a_subsampled_run_inherits_the_full_splits_boundaries() -> None:
    """O-25: the sample is cut at the full frame's cutoff, never at its own quantile."""
    ratings = _ratings_over_days()
    full = temporal_split(ratings)
    # A sample dominated by late activity: its own quantile would move the
    # cutoff (and the 28-day holdout) past the full split's sealed boundary.
    late_heavy = pd.concat(
        [ratings[ratings["userId"] <= 4], ratings[ratings["timestamp"] >= full.cutoff]]
    ).drop_duplicates()
    assert temporal_split(late_heavy).holdout_end > full.holdout_end

    cut = temporal_split(late_heavy, cutoff=temporal_cutoff(ratings))
    params = sealed_partition_params(ratings, cut, cut.train)

    assert (cut.cutoff, cut.holdout_end) == (full.cutoff, full.holdout_end)
    assert params["sealed_boundary_timestamp"] == full.holdout_end
    assert cut.train["timestamp"].max() < full.cutoff
    assert cut.holdout["timestamp"].max() < full.holdout_end
    assert not cut.holdout.empty


def test_run_once_cuts_a_subsample_at_the_full_frames_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow

    import src.training.sasrec as sasrec_training

    ratings = _ratings_over_days().assign(rating=4.0)
    full = temporal_split(ratings)
    seen: list[tuple[int, int]] = []
    original = sasrec_training.temporal_split

    def recording_split(frame: pd.DataFrame, **kwargs: object) -> object:
        result = original(frame, **kwargs)  # type: ignore[arg-type]
        seen.append((result.cutoff, result.holdout_end))
        return result

    monkeypatch.setattr(sasrec_training, "temporal_split", recording_split)
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    previous_uri = mlflow.get_tracking_uri()
    try:
        mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
        sasrec_training.run_once(
            ratings,
            _config(epochs=1, max_sequence_length=4, batch_size=64),
            sample_fraction=0.5,
            run_label="o25",
            artifact_root=tmp_path / "durable",
        )
        run = mlflow.MlflowClient().search_runs(
            [mlflow.get_experiment_by_name(sasrec_training.PHASE_2_EXPERIMENT).experiment_id]
        )[0]
    finally:
        mlflow.set_tracking_uri(previous_uri)

    assert seen == [(full.cutoff, full.holdout_end)]
    assert run.data.params["cutoff_timestamp"] == str(full.cutoff)
    assert run.data.params["sealed_boundary_timestamp"] == str(full.holdout_end)
    assert int(run.data.params["latest_scored_timestamp"]) < full.holdout_end
