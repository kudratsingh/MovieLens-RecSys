"""
Exploratory data analysis on MovieLens 25M in Postgres.

Re-runnable from the project root: `python -m notebooks.eda` (or `make eda`).
Run with `-m` so the project root lands on sys.path and `src.config` resolves;
running `python notebooks/eda.py` directly will fail with ModuleNotFoundError.
Every section
runs as a SQL aggregation so result sets are tiny — we never pull 25 M rows
into pandas. Output is plain text designed to be pasted into docs/eda.md
when iterating on the writeup.

This script computes descriptive statistics only. It deliberately does not
compute any model metric — those go through src/evaluation/ per
non-negotiable #5. The split logic here mirrors src/data/split.py
(80th-percentile cutoff, 28-day holdout) but is expressed in SQL so the
numbers can be verified against the in-Python implementation.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Connection, create_engine, text

from src.config import Settings


def main() -> None:
    settings = Settings()
    engine = create_engine(settings.database_url)

    with engine.connect() as conn:
        _section("1. Scale & sparsity", scale_stats(conn))
        _section("2. Rating distribution", rating_distribution(conn))
        _section("3. User activity (ratings per user)", user_activity_distribution(conn))
        _section("4. Item popularity (ratings per movie)", item_popularity_distribution(conn))
        _section("5. Top 10 most-rated movies", most_rated_movies(conn))
        _section("6. Temporal range", temporal_range(conn))
        _section("7. Split boundary T (per ADR 0001)", split_boundary(conn))
        _section("8. Split sizes", split_sizes(conn))
        _section("9. Cold-start sizing", cold_start_sizing(conn))


def _section(title: str, df: pd.DataFrame) -> None:
    print(f"\n## {title}\n")
    # to_string keeps the markdown-friendly fixed-width look in a terminal.
    print(df.to_string(index=False))
    print()


def scale_stats(conn: Connection) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for table in ("ratings", "movies", "tags", "links"):
        n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        rows.append({"metric": f"{table} rows", "value": f"{int(n):,}"})

    n_users = conn.execute(text('SELECT COUNT(DISTINCT "userId") FROM ratings')).scalar_one()
    n_movies_rated = conn.execute(
        text('SELECT COUNT(DISTINCT "movieId") FROM ratings')
    ).scalar_one()
    n_movies_total = conn.execute(text("SELECT COUNT(*) FROM movies")).scalar_one()

    sparsity = float(n_users) * float(n_movies_rated)
    sparsity_pct = conn.execute(text("SELECT COUNT(*) FROM ratings")).scalar_one() / sparsity * 100

    rows.extend(
        [
            {"metric": "distinct users in ratings", "value": f"{int(n_users):,}"},
            {"metric": "distinct movies in ratings", "value": f"{int(n_movies_rated):,}"},
            {
                "metric": "movies in catalog never rated",
                "value": f"{int(n_movies_total) - int(n_movies_rated):,}",
            },
            {"metric": "sparsity (filled cells)", "value": f"{sparsity_pct:.4f}%"},
        ]
    )
    return pd.DataFrame(rows)


def rating_distribution(conn: Connection) -> pd.DataFrame:
    df = pd.read_sql(
        text("SELECT rating, COUNT(*) AS count FROM ratings GROUP BY rating ORDER BY rating"),
        conn,
    )
    df["pct"] = (df["count"] / df["count"].sum() * 100).round(2)
    df["count"] = df["count"].map(lambda n: f"{n:,}")
    return df


def user_activity_distribution(conn: Connection) -> pd.DataFrame:
    row = conn.execute(text("""
            WITH per_user AS (
                SELECT "userId", COUNT(*) AS n FROM ratings GROUP BY "userId"
            )
            SELECT
                MIN(n) AS min,
                percentile_disc(0.25) WITHIN GROUP (ORDER BY n) AS p25,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY n) AS p50,
                percentile_disc(0.75) WITHIN GROUP (ORDER BY n) AS p75,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY n) AS p95,
                percentile_disc(0.99) WITHIN GROUP (ORDER BY n) AS p99,
                MAX(n) AS max,
                ROUND(AVG(n), 1) AS mean
            FROM per_user
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def item_popularity_distribution(conn: Connection) -> pd.DataFrame:
    row = conn.execute(text("""
            WITH per_movie AS (
                SELECT "movieId", COUNT(*) AS n FROM ratings GROUP BY "movieId"
            )
            SELECT
                MIN(n) AS min,
                percentile_disc(0.25) WITHIN GROUP (ORDER BY n) AS p25,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY n) AS p50,
                percentile_disc(0.75) WITHIN GROUP (ORDER BY n) AS p75,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY n) AS p95,
                percentile_disc(0.99) WITHIN GROUP (ORDER BY n) AS p99,
                MAX(n) AS max,
                ROUND(AVG(n), 1) AS mean
            FROM per_movie
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def most_rated_movies(conn: Connection) -> pd.DataFrame:
    return pd.read_sql(
        text("""
            SELECT
                m.title,
                COUNT(*) AS n_ratings,
                ROUND(AVG(r.rating)::numeric, 2) AS avg_rating
            FROM ratings r JOIN movies m USING ("movieId")
            GROUP BY m.title
            ORDER BY n_ratings DESC
            LIMIT 10
            """),
        conn,
    )


def temporal_range(conn: Connection) -> pd.DataFrame:
    row = conn.execute(text("""
            SELECT
                to_timestamp(MIN(timestamp)) AS earliest,
                to_timestamp(MAX(timestamp)) AS latest,
                (MAX(timestamp) - MIN(timestamp)) / 86400 AS span_days,
                ROUND(((MAX(timestamp) - MIN(timestamp)) / 86400 / 365.0)::numeric, 1)
                    AS span_years
            FROM ratings
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def split_boundary(conn: Connection) -> pd.DataFrame:
    # percentile_disc(0.8) selects an actual value present in the data, matching
    # the method="lower" choice in src/data/split.py. The two numbers must agree.
    row = conn.execute(text("""
            WITH t AS (
                SELECT percentile_disc(0.8) WITHIN GROUP (ORDER BY timestamp)::bigint
                    AS cutoff
                FROM ratings
            )
            SELECT
                cutoff,
                to_timestamp(cutoff) AS cutoff_dt,
                cutoff + 28*86400 AS holdout_end,
                to_timestamp(cutoff + 28*86400) AS holdout_end_dt
            FROM t
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


def split_sizes(conn: Connection) -> pd.DataFrame:
    # One scan over ratings cross-joined with the 1-row cutoff CTE.
    row = conn.execute(text("""
            WITH t AS (
                SELECT percentile_disc(0.8) WITHIN GROUP (ORDER BY timestamp)::bigint
                    AS cutoff
                FROM ratings
            )
            SELECT
                SUM(CASE WHEN r.timestamp < t.cutoff THEN 1 ELSE 0 END) AS train,
                SUM(CASE
                    WHEN r.timestamp >= t.cutoff
                     AND r.timestamp < t.cutoff + 28*86400 THEN 1 ELSE 0 END) AS holdout,
                SUM(CASE WHEN r.timestamp >= t.cutoff + 28*86400 THEN 1 ELSE 0 END)
                    AS test
            FROM ratings r CROSS JOIN t
            """)).mappings().one()
    df = pd.DataFrame([dict(row)])
    total = int(df["train"].iloc[0]) + int(df["holdout"].iloc[0]) + int(df["test"].iloc[0])
    df["total"] = total
    for col in ("train", "holdout", "test"):
        df[f"{col}_pct"] = (df[col].astype(float) / total * 100).round(2)
    return df


def cold_start_sizing(conn: Connection) -> pd.DataFrame:
    # The breakdown that matters for the eval harness: how many warm vs cold
    # vs brand-new users we'll be scoring. Drives expectations for the per-slice
    # metrics that protocol.py reports.
    row = conn.execute(text("""
            WITH t AS (
                SELECT percentile_disc(0.8) WITHIN GROUP (ORDER BY timestamp)::bigint
                    AS cutoff
                FROM ratings
            ),
            train_counts AS (
                SELECT r."userId", COUNT(*) AS n
                FROM ratings r CROSS JOIN t
                WHERE r.timestamp < t.cutoff
                GROUP BY r."userId"
            ),
            holdout_users AS (
                SELECT DISTINCT r."userId"
                FROM ratings r CROSS JOIN t
                WHERE r.timestamp >= t.cutoff
                  AND r.timestamp < t.cutoff + 28*86400
            )
            SELECT
                (SELECT COUNT(*) FROM train_counts WHERE n >= 5) AS warm_in_train,
                (SELECT COUNT(*) FROM train_counts WHERE n < 5) AS cold_in_train,
                (SELECT COUNT(*) FROM holdout_users) AS total_in_holdout,
                (SELECT COUNT(*) FROM holdout_users h
                 WHERE NOT EXISTS (
                     SELECT 1 FROM train_counts tc WHERE tc."userId" = h."userId"
                 )) AS new_in_holdout,
                (SELECT COUNT(*) FROM holdout_users h
                 JOIN train_counts tc ON tc."userId" = h."userId"
                 WHERE tc.n < 5) AS cold_in_holdout,
                (SELECT COUNT(*) FROM holdout_users h
                 JOIN train_counts tc ON tc."userId" = h."userId"
                 WHERE tc.n >= 5) AS warm_in_holdout
            """)).mappings().one()
    return pd.DataFrame([dict(row)])


if __name__ == "__main__":
    main()
