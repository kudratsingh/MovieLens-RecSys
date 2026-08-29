"""ADR 0011's synthetic cold-start cohort: generation, loading, and the
trainer-side glue that turns it into per-bucket MLflow metrics.

The cohort exists to make one claim falsifiable rather than asserted — that a
model routes a user below ADR 0001's ``COLD_START_THRESHOLD`` to its popularity
fallback and a user above it to its learned path. MovieLens's own cold tail
cannot support that claim per bucket, so this package builds controlled ones.
"""
