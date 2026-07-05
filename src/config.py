"""
Application settings, loaded from environment variables (or a .env file).

This is the single source of truth for DB credentials, auth-provider
config, and filesystem paths. Downstream modules accept a Settings
instance — they never reach for os.environ themselves. That makes
overrides explicit and tests easy.

Bundle #1b (ADR 0007 + ADR 0008) added the auth + tenancy fields:

  * ``app_user_*`` / ``admin_user_*`` — per-role Postgres credentials.
    The FastAPI service uses the app_user URL (RLS applies); Prefect
    and offline scripts use admin_user (BYPASSRLS).
  * pgBouncer front-door URLs point at ``localhost:6432`` in dev; the
    ``movielens_app`` / ``movielens_admin`` database aliases pin the
    upstream role (see infra/pgbouncer/pgbouncer.ini).
  * ``keycloak_*`` — Keycloak base URL and JWKS cache TTL for the
    auth middleware.
  * ``dev_auth_bypass`` — dev-only short-circuit that skips token
    validation. The __init__ assertion refuses to construct Settings
    with the bypass on in any non-dev environment.
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

    # --- Environment ---------------------------------------------------------

    # Names the deployment. Runtime asserts that dev_auth_bypass is only
    # ever set in "dev" (see __init__ below). Production builds set this
    # via ENVIRONMENT=production in the container's base layer.
    environment: str = "dev"

    # --- Postgres (superuser, used by migrations + Phase 1/2 scripts) --------

    postgres_user: str = "recsys"
    postgres_password: str = "recsys"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "movielens"

    # --- Postgres (per-role, added in Bundle #1b) ----------------------------
    #
    # The FastAPI service connects via pgBouncer (port 6432) using the
    # movielens_app database alias, which pins upstream user = app_user.
    # RLS applies. Prefect DAGs and offline scripts use the admin_user
    # URL — direct 5432, BYPASSRLS. Dev defaults match compose.
    app_user_db_user: str = "app_user"
    app_user_db_password: str = "app_user"
    app_user_db_host: str = "localhost"
    app_user_db_port: int = 6432  # pgBouncer, not postgres direct
    app_user_db_name: str = "movielens_app"  # alias that pins upstream role

    admin_user_db_user: str = "admin_user"
    admin_user_db_password: str = "admin_user"
    admin_user_db_host: str = "localhost"
    admin_user_db_port: int = 5432  # direct postgres, not pooled
    admin_user_db_name: str = "movielens"

    # --- Auth (Keycloak, added in Bundle #1b) --------------------------------

    keycloak_base_url: str = "http://localhost:8080"
    # JWKS cache TTL in seconds. ADR 0007 §risks pins 5 minutes: long
    # enough that we're not fetching JWKS every request, short enough
    # that a Keycloak-side key rotation propagates within one TTL.
    jwks_cache_ttl_seconds: int = 300
    # The audience/client_id we expect on every access token. Every
    # realm's movielens-api client is named identically per the seed
    # realm JSONs — a token issued for a different client is rejected.
    keycloak_audience: str = "movielens-api"
    # Dev-only bypass. When True, the middleware skips token validation
    # and treats every request as coming from `dev_bypass_tenant`. The
    # constructor asserts this is only set when environment == "dev".
    dev_auth_bypass: bool = False
    dev_bypass_tenant: str = "default"
    dev_bypass_user: str = "dev-user"

    # --- Data / DVC ----------------------------------------------------------

    raw_data_dir: Path = Path("data/raw")

    # --- MLflow --------------------------------------------------------------

    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_experiment: str = "phase-1-baselines"

    # --- Derived --------------------------------------------------------------

    @property
    def database_url(self) -> str:
        """Superuser URL for migrations + Phase 1/2 offline scripts."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def app_user_database_url(self) -> str:
        """RLS-applied URL used by the FastAPI service. Routes through
        pgBouncer's movielens_app alias — upstream user is app_user."""
        return (
            f"postgresql+psycopg2://{self.app_user_db_user}:{self.app_user_db_password}"
            f"@{self.app_user_db_host}:{self.app_user_db_port}/{self.app_user_db_name}"
        )

    @property
    def admin_user_database_url(self) -> str:
        """BYPASSRLS URL used by Prefect DAGs and offline materialization."""
        return (
            f"postgresql+psycopg2://{self.admin_user_db_user}:{self.admin_user_db_password}"
            f"@{self.admin_user_db_host}:{self.admin_user_db_port}/{self.admin_user_db_name}"
        )

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        # Guardrail from ADR 0007 §decision — dev_auth_bypass exists for
        # local dev only. A production image that boots with the bypass
        # on is an unauthenticated production path (non-negotiable #10).
        # Assert at construction time so misconfig fails before the app
        # can accept a request.
        if self.dev_auth_bypass and self.environment != "dev":
            raise RuntimeError(
                f"dev_auth_bypass=True is only permitted when environment='dev'; "
                f"got environment={self.environment!r}. Refusing to construct Settings."
            )
