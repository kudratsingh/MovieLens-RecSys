"""
Application settings, loaded from environment variables (or a .env file).

This is the single source of truth for DB credentials and filesystem paths.
Downstream modules accept a Settings instance — they never reach for
os.environ themselves. That makes overrides explicit and tests easy.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Postgres. Defaults mirror docker-compose.yml so `make infra-up && make data-ingest`
    # works out of the box; override via env in any non-local environment.
    postgres_user: str = "recsys"
    postgres_password: str = "recsys"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "movielens"

    # Where raw datasets live on disk. DVC tracks the contents; .gitignore excludes them.
    raw_data_dir: Path = Path("data/raw")

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
