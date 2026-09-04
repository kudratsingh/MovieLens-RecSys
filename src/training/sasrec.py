"""Train and evaluate SASRec through the shared candidate-stage protocol."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import mlflow
import pandas as pd

from src.config import Settings
from src.data.split import temporal_split
from src.evaluation.protocol import COLD_START_THRESHOLD, K_CANDIDATES, evaluate
from src.models.candidates.sasrec import SASRecConfig, SASRecModel, gbce_beta
from src.training.twotower import (
    INPUT_DIR_ENV_VAR,
    PHASE_2_EXPERIMENT,
    load_inputs,
    resolve_sample_fraction,
    subsample_users,
)

logger = logging.getLogger(__name__)
RUN_LABEL_ENV_VAR = "SASREC_RUN_LABEL"


def run_once(
    ratings: pd.DataFrame,
    config: SASRecConfig,
    *,
    sample_fraction: float = 1.0,
    run_label: str = "",
) -> None:
    if sample_fraction != 1.0:
        ratings = subsample_users(ratings, sample_fraction, config.seed)
    split = temporal_split(ratings)
    train_counts = split.train.groupby("userId").size().to_dict()
    holdout = split.holdout.groupby("userId")["movieId"].apply(set).to_dict()
    user_ids = list(holdout)
    model = SASRecModel(config=config, cold_start_threshold=COLD_START_THRESHOLD)

    mlflow.set_experiment(PHASE_2_EXPERIMENT)
    run_name = "sasrec" if not run_label else f"sasrec-{run_label}"
    with mlflow.start_run(run_name=run_name):
        mlflow.set_tags(
            {
                "model_family": "candidate_generator",
                "model_type": "sasrec",
                "stage": "candidate",
                "sweep_label": run_label,
            }
        )
        mlflow.log_params(
            {
                **config.as_params(),
                "user_sample_fraction": sample_fraction,
                "cutoff_timestamp": split.cutoff,
                "n_train_rows": len(split.train),
                "n_holdout_rows": len(split.holdout),
                "k_candidates": K_CANDIDATES,
            }
        )

        def on_epoch(epoch: int, loss: float) -> None:
            mlflow.log_metric("train_loss", loss, step=epoch)
            model.build_index()
            recommendations = model.recommend_for_users(user_ids, K_CANDIDATES)
            result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
            mlflow.log_metric("epoch_warm_recall_at_k_candidates", result.warm.recall, step=epoch)
            logger.info(
                "Epoch %d loss=%.4f warm recall@%d=%.4f",
                epoch,
                loss,
                K_CANDIDATES,
                result.warm.recall,
            )

        started = time.perf_counter()
        model.fit(split.train, on_epoch=on_epoch)
        fit_seconds = time.perf_counter() - started
        recommendations = model.recommend_for_users(user_ids, K_CANDIDATES)
        result = evaluate(recommendations, holdout, train_counts, k=K_CANDIDATES)
        beta = (
            1.0
            if config.loss == "bce"
            else gbce_beta(
                negative_count=config.negative_count,
                catalog_size=len(model._index_to_item),
                calibration_t=config.calibration_t,
            )
        )
        mlflow.log_params(
            {
                "fit_seconds": round(fit_seconds, 1),
                "n_items_in_train": len(model._index_to_item),
                "n_users_in_train": len(model._user_history),
                "gbce_beta": beta,
            }
        )
        mlflow.log_metrics(
            {
                "warm_recall_at_k_candidates": result.warm.recall,
                "warm_ndcg_at_k_candidates": result.warm.ndcg,
                "cold_recall_at_k_candidates": result.cold.recall,
                "cold_ndcg_at_k_candidates": result.cold.ndcg,
                "overall_recall_at_k_candidates": result.overall.recall,
                "overall_ndcg_at_k_candidates": result.overall.ndcg,
            }
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    input_dir_raw = os.environ.get(INPUT_DIR_ENV_VAR, "").strip()
    ratings, _movies = load_inputs(
        settings, input_dir=Path(input_dir_raw) if input_dir_raw else None
    )
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    run_once(
        ratings,
        SASRecConfig.from_env(),
        sample_fraction=resolve_sample_fraction(),
        run_label=os.environ.get(RUN_LABEL_ENV_VAR, "").strip(),
    )


if __name__ == "__main__":
    main()
