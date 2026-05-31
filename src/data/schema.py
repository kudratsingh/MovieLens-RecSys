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
