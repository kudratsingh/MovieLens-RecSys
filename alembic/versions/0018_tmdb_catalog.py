"""create the normalised TMDB catalog tables

Revision ID: 0018_tmdb_catalog
Revises: 0017_request_audits

ADR 0017's first increment represented an item by its MovieLens genres and its
release year, and the measurement said that is too coarse: it reached 4,998
cold items and scored 0.0001 recall doing it, while 3,413 items have no genres
at all and cannot be represented by that increment even in principle. Increment
2 is TMDB metadata, and these tables are where the snapshot lands.

**No row-level security on any table here, deliberately.** ADR 0008 puts forced
RLS on tenant-scoped tables because they hold one tenant's ratings, product
state and audit rows, and cross-tenant leakage is this system's highest-severity
bug class. None of that applies to a film's runtime, its cast list or its
keywords: this is catalog data, public in origin, identical for every tenant,
and carrying no `tenant_id` to key a policy on. `movie_catalog_metadata`
(migration 0011) made the same call for the same reason, and the tenant
isolation suite already documents that table as global by design. The access
grants follow: `app_user` reads, `admin_user` writes.

**Six columns are not point-in-time safe and are marked as such in the database
itself.** `vote_average`, `vote_count`, `popularity`, `budget`, `revenue` and
`status` describe the film as TMDB saw it on the day of the pull. There is no
per-observation timestamp on them, so no point-in-time join can reconstruct what
they were during the MovieLens interaction history — which ends in 2019, years
before the snapshot. Any one of them used as a ranker feature against the ADR
0001 temporal split leaks the future into training by construction, and would
inflate offline metrics silently, which is the exact failure mode CLAUDE.md's
leakage warning names. They are stored because they are worth having for
analysis and for the product's crowd-score display, and they carry a `COMMENT`
saying what they are. `tests/unit/test_tmdb_leakage.py` fails if any of them
reaches `src/feature_contract.py`.

The static attributes — genres, overview, keywords, cast, crew, runtime, release
date, original language, collection, certification — do not move once the film
exists, and those are the intended features.

`movie_id` is the primary key of `tmdb_movies`, not `tmdb_id`. 34 TMDB ids in
`links.csv` are claimed by two MovieLens movies each (69 rows), which are
duplicate catalog entries for one film; `tmdb_id` is therefore indexed but not
unique, and one fetched payload lands as two rows. Keying on `movie_id` also
means every join to `ratings`, `movies` and `movie_catalog_metadata` is a
straight equality on the id those tables already use.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_tmdb_catalog"
down_revision: str | None = "0017_request_audits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors `src/data/tmdb_schema.NOT_POINT_IN_TIME_SAFE_COLUMNS`.
AS_OF_PULL_COLUMNS: tuple[str, ...] = (
    "vote_average",
    "vote_count",
    "popularity",
    "budget",
    "revenue",
    "status",
)

AS_OF_PULL_COMMENT = (
    "not point-in-time safe: as-of-pull value, has no observation timestamp and "
    "must never be used as a model feature against the ADR 0001 temporal split"
)

TABLES: tuple[str, ...] = (
    "tmdb_movies",
    "tmdb_movie_genres",
    "tmdb_keywords",
    "tmdb_movie_keywords",
    "tmdb_people",
    "tmdb_movie_cast",
    "tmdb_movie_crew",
    "tmdb_production_companies",
    "tmdb_movie_production_companies",
    "tmdb_production_countries",
    "tmdb_spoken_languages",
    "tmdb_release_dates",
)


def upgrade() -> None:
    op.create_table(
        "tmdb_movies",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("tmdb_id", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("original_title", sa.Text, nullable=True),
        sa.Column("overview", sa.Text, nullable=True),
        sa.Column("tagline", sa.Text, nullable=True),
        sa.Column("release_date", sa.Date, nullable=True),
        sa.Column("runtime", sa.Integer, nullable=True),
        sa.Column("original_language", sa.Text, nullable=True),
        sa.Column("adult", sa.Boolean, nullable=True),
        # not point-in-time safe: as-of-pull values (see the module docstring)
        sa.Column("budget", sa.BigInteger, nullable=True),
        sa.Column("revenue", sa.BigInteger, nullable=True),
        sa.Column("status", sa.Text, nullable=True),
        sa.Column("vote_average", sa.Float, nullable=True),
        sa.Column("vote_count", sa.Integer, nullable=True),
        sa.Column("popularity", sa.Float, nullable=True),
        # end of the as-of-pull block
        sa.Column("collection_id", sa.Integer, nullable=True),
        sa.Column("collection_name", sa.Text, nullable=True),
        sa.Column("poster_path", sa.Text, nullable=True),
        sa.Column("backdrop_path", sa.Text, nullable=True),
        sa.Column("imdb_id", sa.Text, nullable=True),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.movieId"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", name="pk_tmdb_movies"),
    )
    op.create_index("idx_tmdb_movies_tmdb_id", "tmdb_movies", ["tmdb_id"])
    op.create_index("idx_tmdb_movies_collection", "tmdb_movies", ["collection_id"])
    for column in AS_OF_PULL_COLUMNS:
        op.execute(f"COMMENT ON COLUMN tmdb_movies.{column} IS '{AS_OF_PULL_COMMENT}';")

    op.create_table(
        "tmdb_movie_genres",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("genre_id", sa.Integer, nullable=False),
        sa.Column("genre_name", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "genre_id", name="pk_tmdb_movie_genres"),
    )
    op.create_index("idx_tmdb_movie_genres_genre", "tmdb_movie_genres", ["genre_id"])

    op.create_table(
        "tmdb_keywords",
        sa.Column("keyword_id", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.PrimaryKeyConstraint("keyword_id", name="pk_tmdb_keywords"),
    )

    op.create_table(
        "tmdb_movie_keywords",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("keyword_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["keyword_id"], ["tmdb_keywords.keyword_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "keyword_id", name="pk_tmdb_movie_keywords"),
    )
    op.create_index("idx_tmdb_movie_keywords_keyword", "tmdb_movie_keywords", ["keyword_id"])

    op.create_table(
        "tmdb_people",
        sa.Column("person_id", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("original_name", sa.Text, nullable=True),
        sa.Column("gender", sa.SmallInteger, nullable=True),
        sa.Column("known_for_department", sa.Text, nullable=True),
        sa.Column("profile_path", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("person_id", name="pk_tmdb_people"),
    )

    # Keyed on (movie_id, credit_id). `credit_id` has to be in the key because
    # (movie_id, person_id) is not unique — the same actor can hold two billed
    # roles in one film. `movie_id` has to be in it too, because the 34
    # duplicated TMDB ids put the same credit on two MovieLens movies.
    op.create_table(
        "tmdb_movie_cast",
        sa.Column("credit_id", sa.Text, nullable=False),
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("person_id", sa.Integer, nullable=False),
        sa.Column("character", sa.Text, nullable=True),
        sa.Column("cast_order", sa.SmallInteger, nullable=True),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["tmdb_people.person_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "credit_id", name="pk_tmdb_movie_cast"),
    )
    op.create_index("idx_tmdb_movie_cast_movie", "tmdb_movie_cast", ["movie_id", "cast_order"])
    op.create_index("idx_tmdb_movie_cast_person", "tmdb_movie_cast", ["person_id"])

    op.create_table(
        "tmdb_movie_crew",
        sa.Column("credit_id", sa.Text, nullable=False),
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("person_id", sa.Integer, nullable=False),
        sa.Column("department", sa.Text, nullable=True),
        sa.Column("job", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["tmdb_people.person_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "credit_id", name="pk_tmdb_movie_crew"),
    )
    op.create_index("idx_tmdb_movie_crew_movie", "tmdb_movie_crew", ["movie_id", "job"])
    op.create_index("idx_tmdb_movie_crew_person", "tmdb_movie_crew", ["person_id"])

    op.create_table(
        "tmdb_production_companies",
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("origin_country", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("company_id", name="pk_tmdb_production_companies"),
    )

    op.create_table(
        "tmdb_movie_production_companies",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("company_id", sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["tmdb_production_companies.company_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint(
            "movie_id", "company_id", name="pk_tmdb_movie_production_companies"
        ),
    )
    op.create_index(
        "idx_tmdb_movie_companies_company", "tmdb_movie_production_companies", ["company_id"]
    )

    op.create_table(
        "tmdb_production_countries",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("iso_3166_1", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "iso_3166_1", name="pk_tmdb_production_countries"),
    )

    op.create_table(
        "tmdb_spoken_languages",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("iso_639_1", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("english_name", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("movie_id", "iso_639_1", name="pk_tmdb_spoken_languages"),
    )

    # One row per (country, release type, index). A film routinely has both a
    # theatrical and a digital date in the same country, and the certification
    # that matters hangs off a specific one of them; `release_index` breaks the
    # tie when TMDB lists two entries of the same type, which it does for
    # re-releases.
    op.create_table(
        "tmdb_release_dates",
        sa.Column("movie_id", sa.Integer, nullable=False),
        sa.Column("iso_3166_1", sa.Text, nullable=False),
        sa.Column("release_type", sa.SmallInteger, nullable=False),
        sa.Column("release_index", sa.SmallInteger, nullable=False),
        sa.Column("certification", sa.Text, nullable=True),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("iso_639_1", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["movie_id"], ["tmdb_movies.movie_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "movie_id",
            "iso_3166_1",
            "release_type",
            "release_index",
            name="pk_tmdb_release_dates",
        ),
    )
    op.create_index(
        "idx_tmdb_release_dates_country",
        "tmdb_release_dates",
        ["iso_3166_1", "certification"],
    )

    for table in TABLES:
        op.execute(f"GRANT SELECT ON {table} TO app_user;")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO admin_user;")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM admin_user;")
        op.execute(f"REVOKE SELECT ON {table} FROM app_user;")

    op.drop_index("idx_tmdb_release_dates_country", table_name="tmdb_release_dates")
    op.drop_table("tmdb_release_dates")
    op.drop_table("tmdb_spoken_languages")
    op.drop_table("tmdb_production_countries")
    op.drop_index("idx_tmdb_movie_companies_company", table_name="tmdb_movie_production_companies")
    op.drop_table("tmdb_movie_production_companies")
    op.drop_table("tmdb_production_companies")
    op.drop_index("idx_tmdb_movie_crew_person", table_name="tmdb_movie_crew")
    op.drop_index("idx_tmdb_movie_crew_movie", table_name="tmdb_movie_crew")
    op.drop_table("tmdb_movie_crew")
    op.drop_index("idx_tmdb_movie_cast_person", table_name="tmdb_movie_cast")
    op.drop_index("idx_tmdb_movie_cast_movie", table_name="tmdb_movie_cast")
    op.drop_table("tmdb_movie_cast")
    op.drop_table("tmdb_people")
    op.drop_index("idx_tmdb_movie_keywords_keyword", table_name="tmdb_movie_keywords")
    op.drop_table("tmdb_movie_keywords")
    op.drop_table("tmdb_keywords")
    op.drop_index("idx_tmdb_movie_genres_genre", table_name="tmdb_movie_genres")
    op.drop_table("tmdb_movie_genres")
    op.drop_index("idx_tmdb_movies_collection", table_name="tmdb_movies")
    op.drop_index("idx_tmdb_movies_tmdb_id", table_name="tmdb_movies")
    op.drop_table("tmdb_movies")
