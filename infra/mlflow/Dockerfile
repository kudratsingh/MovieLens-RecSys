# The official MLflow image is slim and ships without database drivers.
# Our compose stack points MLflow's --backend-store-uri at a Postgres
# database, so the psycopg2 driver must be in the image or the container
# crashes on startup with ModuleNotFoundError. Pinned to match the
# psycopg2-binary version the rest of the project uses for consistency.
FROM ghcr.io/mlflow/mlflow:v2.13.0

RUN pip install --no-cache-dir psycopg2-binary==2.9.10
