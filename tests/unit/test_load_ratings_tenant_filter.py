"""The training frame is one tenant's rows, not the whole table.

Synthetic tenants share the `ratings` table with MovieLens by design — that is
what makes demo personas exercise the real serving path. The cost is that an
unfiltered read is silently machine-dependent: it returns 25,000,095 rows on a
clean checkout and 25,000,610 on a machine where `make demo-seed` has run. These
tests pin the filter so that never becomes a training input again.

SQLite stands in for Postgres here. It is enough because the thing under test is
the SQL's WHERE clause and the parameter binding, neither of which is
dialect-specific, and it keeps the test in the unit suite where it runs on every
PR rather than only where a live database exists.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text

from src.data.load import TRAINING_TENANT_ID, load_ratings

# Deliberately mirrors production: demo user ids sit far above MovieLens's
# 1..162541 range, so a collision cannot mask a missing filter.
_DEFAULT_ROWS = [(1, 10, 4.0, 1_000), (2, 20, 3.5, 2_000), (162541, 30, 5.0, 3_000)]
_DEMO_ROWS = [(900000101, 10, 5.0, 4_000), (900000205, 40, 1.0, 5_000)]


@pytest.fixture
def engine() -> Engine:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                'CREATE TABLE ratings ("userId" INTEGER NOT NULL, '
                '"movieId" INTEGER NOT NULL, rating REAL NOT NULL, '
                "timestamp BIGINT NOT NULL, tenant_id TEXT NOT NULL)"
            )
        )
        for tenant, rows in (("default", _DEFAULT_ROWS), ("demo", _DEMO_ROWS)):
            for user_id, movie_id, rating, ts in rows:
                connection.execute(
                    text("INSERT INTO ratings VALUES " "(:u, :m, :r, :t, :tenant)"),
                    {"u": user_id, "m": movie_id, "r": rating, "t": ts, "tenant": tenant},
                )
    return engine


def test_the_default_read_excludes_every_synthetic_tenant(engine: Engine) -> None:
    frame = load_ratings(engine)

    assert len(frame) == len(_DEFAULT_ROWS)
    assert sorted(frame["userId"]) == [1, 2, 162541]
    # The specific failure this guards: a demo persona's rating counting toward
    # item popularity in a model that serves MovieLens users.
    assert 900000101 not in set(frame["userId"])


def test_the_frame_does_not_grow_when_a_synthetic_tenant_is_seeded(engine: Engine) -> None:
    before = load_ratings(engine)

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO ratings VALUES (900000999, 50, 2.0, 6000, 'demo')"))

    after = load_ratings(engine)
    pd.testing.assert_frame_equal(before, after)


def test_a_synthetic_tenant_can_still_be_read_on_purpose(engine: Engine) -> None:
    # The filter constrains the default, it does not make other tenants
    # unreachable — the demo bundle's own training needs exactly this.
    frame = load_ratings(engine, tenant_id="demo")

    assert sorted(frame["userId"]) == [900000101, 900000205]


def test_an_unknown_tenant_reads_empty_rather_than_everything(engine: Engine) -> None:
    # A typo in a tenant id must not degrade to "no filter".
    assert load_ratings(engine, tenant_id="nope").empty


def test_the_training_tenant_is_the_movielens_one() -> None:
    assert TRAINING_TENANT_ID == "default"
