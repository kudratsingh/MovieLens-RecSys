"""
SQLAlchemy table definitions for the MovieLens dataset.

These mirror the CSV schemas from the 25M release exactly.
The timestamp column on ratings is Unix epoch seconds (as shipped).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Engine,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

# Base table shape — the four tables the MovieLens ingest path creates.
# The Phase 3 tenant scaffolding (`public.tenants` registry, `tenant_id`
# column on `ratings` and `tags`, RLS policies) is added by Alembic
# migrations, not by `create_all`. Two reasons: (a) `create_all` runs on
# fresh ingests as a one-shot, whereas migrations run on both fresh and
# existing DBs, so migrations are the natural home for the additive
# tenant changes; (b) RLS policies can't be expressed in SQLAlchemy's
# schema DSL — putting them in migrations keeps schema.py declarative
# without a code-vs-DB drift trap.
#
# Workflow: `make data-ingest` creates base tables + loads CSVs;
# `make db-migrate` layers the tenant scaffolding on top. For a full
# reset, `docker compose down -v` is the clean-slate command — a
# post-migrate `--reset` would drop tables the migrations own and
# leave alembic_version out of sync.
ratings = Table(
    "ratings",
    metadata,
    Column("userId", Integer, nullable=False),
    Column("movieId", Integer, nullable=False),
    Column("rating", Float, nullable=False),
    Column("timestamp", BigInteger, nullable=False),
)

movies = Table(
    "movies",
    metadata,
    Column("movieId", Integer, primary_key=True),
    Column("title", String(512), nullable=False),
    Column("genres", Text, nullable=False),  # pipe-separated
)

tags = Table(
    "tags",
    metadata,
    Column("userId", Integer, nullable=False),
    Column("movieId", Integer, nullable=False),
    # Tags are user-generated free text; MovieLens 25M ships rows with empty
    # tag values, which pandas reads as NaN and SQLAlchemy converts to NULL.
    # Keep them as NULL rather than dropping the rows — the (user, movie,
    # timestamp) triple is still an interaction signal even without a tag string.
    Column("tag", Text, nullable=True),
    Column("timestamp", BigInteger, nullable=False),
)

links = Table(
    "links",
    metadata,
    Column("movieId", Integer, primary_key=True),
    Column("imdbId", String(20)),
    Column("tmdbId", String(20)),
)


def create_tables(engine: Engine) -> None:
    metadata.create_all(engine)
