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
