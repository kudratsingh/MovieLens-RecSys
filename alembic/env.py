"""
Alembic environment. Connects using the URL from Settings (Postgres
superuser in dev). Runs migrations offline (SQL script) or online (live
connection); both paths are exposed via the standard Alembic CLI.

The tenant-scoped tables' schema is *not* driven by autogenerate here —
schema.py stays the runtime source of truth for existing tables and
migrations for tenant additions (roles, public.tenants, RLS policies)
are hand-written. This ADR 0008 workflow is deliberate: RLS policies
are load-bearing and not something we want the autogenerate tooling to
diff against silently.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.config import Settings
from src.data.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve DB URL from application settings so we don't drift from the
# rest of the codebase. `sqlalchemy.url` in alembic.ini stays blank.
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", Settings().database_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
