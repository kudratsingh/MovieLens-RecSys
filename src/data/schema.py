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
    Column("tag", Text, nullable=False),
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
