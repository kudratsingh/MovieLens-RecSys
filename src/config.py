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
  * ``pgbouncer_admin_*`` — the pooler's own admin console credentials,
    read once at boot by the transaction-pool-mode startup check.
  * ``keycloak_*`` — Keycloak base URL and JWKS cache TTL for the
    auth middleware.
  * ``dev_auth_bypass`` — dev-only short-circuit that skips token
    validation. The __init__ assertion refuses to construct Settings
    with the bypass on in any non-dev environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The dev-only credential literals the constructor refuses outside dev.
# Named once so a default and the guard that rejects it cannot drift apart.
DEV_MODEL_SERVER_AUTH_TOKEN = "dev-model-server-token"
DEV_PGBOUNCER_ADMIN_PASSWORD = "pgbouncer_admin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment ---------------------------------------------------------

    # Names the deployment. Every dev-only credential guard below keys off
    # it, which is why it is a Literal and not a bare str: "prod",
    # "Production" or a typo would satisfy each `!= "dev"` test while the
    # process kept behaving like a development box. Production builds set
    # ENVIRONMENT=production in the container's base layer (infra/api and
    # infra/features Dockerfiles), so forgetting the variable in a
    # deployment panel cannot quietly downgrade the deployment.
    environment: Literal["dev", "staging", "production"] = "dev"

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

    # --- pgBouncer admin console --------------------------------------------
    #
    # Read once at boot by src/serving/startup_checks.py, which opens
    # pgBouncer's `pgbouncer` pseudo-database at app_user_db_host:port to
    # prove the pooler is in transaction mode. The defaults match
    # infra/pgbouncer/userlist.txt so dev keeps working with no config; the
    # constructor refuses the default password anywhere else, because this
    # is the pooler's admin credential and it is published in a public repo.
    pgbouncer_admin_user: str = "pgbouncer_admin"
    pgbouncer_admin_password: SecretStr = SecretStr(DEV_PGBOUNCER_ADMIN_PASSWORD)

    # --- Auth (Keycloak, added in Bundle #1b) --------------------------------

    # Internal URL used by the API to fetch discovery/JWKS. In Compose this is
    # the service DNS name; it is deliberately separate from the issuer URL
    # embedded in browser-facing tokens.
    keycloak_base_url: str = "http://localhost:8080"
    # Public origin Keycloak writes into the token issuer claim. Signature
    # verification is not sufficient without pinning this trusted issuer
    # origin: a same-key token must not be able to invent another issuer URL.
    keycloak_public_base_url: str = "http://localhost:8080"
    # JWKS cache TTL in seconds. ADR 0007 §risks pins 5 minutes: long
    # enough that we're not fetching JWKS every request, short enough
    # that a Keycloak-side key rotation propagates within one TTL.
    jwks_cache_ttl_seconds: int = 300
    # The resource audience every access token must contain. Both the
    # confidential load/API client and the public PKCE browser client receive
    # this audience through the realm's OIDC audience mapper.
    keycloak_audience: str = "movielens-api"
    # `azp` identifies the client that obtained the token. Both callers are
    # intentional; an admin-console or unrelated realm client remains rejected.
    keycloak_authorized_parties: tuple[str, ...] = (
        "movielens-api",
        "movielens-web",
    )
    # The confidential client is the only caller that may impersonate demo
    # personas without an explicit realm role. Keep this separate from the
    # resource audience so deployments can rename either setting safely.
    keycloak_service_client_id: str = "movielens-api"
    # Dev-only bypass. When True, the middleware skips token validation
    # and treats every request as coming from `dev_bypass_tenant`. The
    # constructor asserts this is only set when environment == "dev".
    dev_auth_bypass: bool = False
    dev_bypass_tenant: str = "default"
    dev_bypass_user: str = "dev-user"

    # --- Movie metadata (TMDB, server-side only) ----------------------------

    tmdb_read_access_token: SecretStr | None = None
    tmdb_api_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base_url: str = "https://image.tmdb.org/t/p"
    tmdb_timeout_seconds: float = Field(default=2.0, gt=0)
    tmdb_cache_ttl_seconds: int = Field(default=21_600, gt=0)
    tmdb_cache_max_entries: int = Field(default=2_048, gt=0)

    # --- Data / DVC ----------------------------------------------------------

    raw_data_dir: Path = Path("data/raw")

    # --- MLflow --------------------------------------------------------------

    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_experiment: str = "phase-1-baselines"

    # --- Feast / online features -------------------------------------------

    redis_connection_string: str = "localhost:6379"
    feast_repo_path: Path = Path("src/features/feast_repo")
    feast_feature_server_url: str = "http://localhost:6566"

    # --- Learned model sidecar ---------------------------------------------

    model_server_url: str = "http://localhost:6570"
    model_server_timeout_seconds: float = Field(default=0.5, gt=0)
    model_server_auth_token: SecretStr = SecretStr(DEV_MODEL_SERVER_AUTH_TOKEN)
    model_artifact_dir: Path = Path("models/serving")
    model_manifest_name: str = "manifest.json"
    model_tenant_id: str = "demo"
    model_feature_cache_max_entries: int = Field(default=256, gt=0)

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

    def __init__(self, **data: Any) -> None:
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
        if self.keycloak_service_client_id not in self.keycloak_authorized_parties:
            raise RuntimeError(
                "keycloak_service_client_id must be listed in " "keycloak_authorized_parties"
            )
        if (
            self.environment != "dev"
            and self.model_server_auth_token.get_secret_value() == DEV_MODEL_SERVER_AUTH_TOKEN
        ):
            raise RuntimeError(
                "the default model-server token is only permitted in development; "
                "set MODEL_SERVER_AUTH_TOKEN for this environment"
            )
        # Same shape, same reason: the pgBouncer admin password is checked
        # into a public repo, and the console it opens can RELOAD the pooler
        # and read every pool's state. The user name is deliberately not
        # guarded — production keeps the `pgbouncer_admin` role name.
        if (
            self.environment != "dev"
            and self.pgbouncer_admin_password.get_secret_value() == DEV_PGBOUNCER_ADMIN_PASSWORD
        ):
            raise RuntimeError(
                "the default pgBouncer admin password is only permitted in development; "
                "set PGBOUNCER_ADMIN_PASSWORD for this environment"
            )
