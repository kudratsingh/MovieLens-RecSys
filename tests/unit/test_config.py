from pathlib import Path

import pytest

from src.config import Settings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear any POSTGRES_* env vars so tests see the in-code defaults regardless of host env."""
    for var in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "RAW_DATA_DIR",
        "MLFLOW_TRACKING_URI",
        "MLFLOW_EXPERIMENT",
        "TMDB_READ_ACCESS_TOKEN",
        "TMDB_API_BASE_URL",
        "TMDB_IMAGE_BASE_URL",
        "TMDB_TIMEOUT_SECONDS",
        "TMDB_CACHE_TTL_SECONDS",
        "TMDB_CACHE_MAX_ENTRIES",
    ):
        monkeypatch.delenv(var, raising=False)


def _defaults(clean_env: None) -> Settings:
    # _env_file=None bypasses any local .env so the test is deterministic on a dev box.
    return Settings(_env_file=None)


def test_defaults_match_docker_compose(clean_env: None) -> None:
    # If these drift from docker-compose.yml, `make data-ingest` against a fresh stack will fail.
    s = _defaults(clean_env)
    assert s.postgres_user == "recsys"
    assert s.postgres_password == "recsys"
    assert s.postgres_db == "movielens"
    assert s.postgres_port == 5432
    assert s.postgres_host == "localhost"


def test_database_url_format(clean_env: None) -> None:
    s = _defaults(clean_env)
    assert s.database_url == "postgresql+psycopg2://recsys:recsys@localhost:5432/movielens"


def test_env_overrides(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    s = Settings(_env_file=None)
    assert s.postgres_host == "db.internal"
    assert s.postgres_port == 6543
    assert "db.internal:6543" in s.database_url


def test_raw_data_dir_default(clean_env: None) -> None:
    s = _defaults(clean_env)
    assert s.raw_data_dir == Path("data/raw")


def test_mlflow_defaults(clean_env: None) -> None:
    # Defaults must point at the local docker-compose MLflow so `make
    # train-popularity` works on a fresh checkout without extra config.
    s = _defaults(clean_env)
    assert s.mlflow_tracking_uri == "http://localhost:5000"
    assert s.mlflow_experiment == "phase-1-baselines"


def test_mlflow_env_overrides(clean_env: None, monkeypatch) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://mlflow.prod.example.com")
    monkeypatch.setenv("MLFLOW_EXPERIMENT", "challenger-runs")
    s = Settings(_env_file=None)
    assert s.mlflow_tracking_uri == "https://mlflow.prod.example.com"
    assert s.mlflow_experiment == "challenger-runs"


def test_tmdb_is_disabled_by_default_and_token_is_secret(clean_env: None) -> None:
    settings = _defaults(clean_env)
    assert settings.tmdb_read_access_token is None


def test_tmdb_token_loads_from_environment_as_secret(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "server-secret")
    settings = Settings(_env_file=None)

    assert settings.tmdb_read_access_token is not None
    assert settings.tmdb_read_access_token.get_secret_value() == "server-secret"
    assert "server-secret" not in repr(settings)


def test_non_dev_rejects_default_model_server_token(clean_env: None) -> None:
    with pytest.raises(RuntimeError, match="MODEL_SERVER_AUTH_TOKEN"):
        Settings(_env_file=None, environment="production")


def test_non_dev_accepts_explicit_model_server_token(clean_env: None) -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        model_server_auth_token="production-secret",
    )

    assert settings.model_server_auth_token.get_secret_value() == "production-secret"
