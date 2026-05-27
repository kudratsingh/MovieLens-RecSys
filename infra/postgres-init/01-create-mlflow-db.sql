-- The compose stack runs MLflow with --backend-store-uri pointing at a
-- separate `mlflow` database. The Postgres image's POSTGRES_DB env only
-- creates one DB ("movielens"), so without this script the MLflow
-- container fails on startup with "database mlflow does not exist".
--
-- This file lives in /docker-entrypoint-initdb.d and runs exactly once,
-- when the Postgres data volume is first initialized.
CREATE DATABASE mlflow;
