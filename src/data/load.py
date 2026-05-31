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
from sqlalchemy import Engine


def load_ratings(engine: Engine) -> pd.DataFrame:
    """Return every (userId, movieId, rating, timestamp) row in ratings."""
    return pd.read_sql(
        'SELECT "userId", "movieId", rating, timestamp FROM ratings',
        engine,
    )
