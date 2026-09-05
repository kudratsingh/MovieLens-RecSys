# Two-tower model artifacts

Every two-tower training run writes its fitted item tower before holdout
evaluation. The local destination is
`artifacts/twotower/<mlflow-run-id>/`; `TWOTOWER_ARTIFACT_DIR` may point that
root at another durable volume. The directory is create-only: an existing
model or manifest causes the export to fail rather than replace evidence.

The bundle contains:

- `two-tower-model.zip`: deterministic metadata and NumPy tensor members with
  fixed archive timestamps;
- `two-tower-manifest.json`: the model SHA-256, vocabulary SHA-256, item count,
  embedding/history dimensions, feature-mode flag and retrieval contract.

The metadata carries every `TwoTowerConfig` value, the ordered MovieLens item
vocabulary, cold-start threshold, fitted structured-feature schema and the
complete item-tower state. The state includes the aligned structured feature
matrix, projection and learned gate when item features are enabled. Loading
verifies both checksums, rejects unsafe archive members, reconstructs the item
tower, and rebuilds the configured FAISS index.

The local directory remains the recovery copy and the runner uploads a second
copy to the MLflow run under `model/`. MLflow tags record the archive SHA-256.
Runtime user histories and the popularity fallback remain snapshot-derived
state; callers rebuild them from the protocol-pinned training frame, as the
SASRec artifact loader does today.
