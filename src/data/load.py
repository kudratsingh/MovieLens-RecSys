"""
Read the ratings table out of Postgres into a DataFrame.

A thin wrapper around pd.read_sql, kept as its own module so the SQL stays
in one place and downstream code (training, EDA, future feature pipelines)
doesn't reach into Postgres directly. At 25M rows this materializes a
~400 MB DataFrame — fine on a development machine, not fine in a worker.
When that becomes a constraint, swap the implementation for a chunked
read; the call site doesn't have to change.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text

from src.config import DEFAULT_TENANT_ID

# The tenant whose ratings constitute the training frame. MovieLens users live
# in `default`; synthetic tenants (`demo` and friends) share the same table,
# which is the point of row-level security but also means an unfiltered read
# quietly returns them too.
TRAINING_TENANT_ID = DEFAULT_TENANT_ID

_RATINGS_QUERY = text(
    'SELECT "userId", "movieId", rating, timestamp FROM ratings ' "WHERE tenant_id = :tenant_id"
)


def load_ratings(engine: Engine, tenant_id: str = TRAINING_TENANT_ID) -> pd.DataFrame:
    """Return one tenant's (userId, movieId, rating, timestamp) rows.

    The tenant filter is not an optimization. Demo personas are seeded into
    this same table as ordinary rows — that is what makes them exercise the
    real path — so a read without a `tenant_id` predicate hands every trainer
    a frame whose size depends on whether anyone has run `make demo-seed` on
    that machine. That breaks reproducibility in the most annoying way
    available: silently, and only on machines where the demo has been used.
    Defaulting rather than requiring the argument keeps every existing call
    site correct without having to state the obvious at each one.
    """
    return pd.read_sql(_RATINGS_QUERY, engine, params={"tenant_id": tenant_id})
