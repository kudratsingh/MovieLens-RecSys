"""Normalised TMDB catalog tables.

These mirror migration ``0018_tmdb_catalog`` exactly. The migration is
authoritative — it is what creates the tables, the indexes and the grants, and
it is what a fresh database runs. This module exists so the loader has typed
``Table`` objects to insert through instead of hand-written SQL strings, the
same division `src/data/schema.py` draws for the four MovieLens tables.

**No RLS, on purpose.** Every table here is catalog data: facts about films,
identical for every tenant, derived from a public API and a public dataset.
Tenant-scoped tables carry ``tenant_id`` and forced row-level security under
ADR 0008 because they hold one tenant's ratings, state and audits; a film's
runtime is not one tenant's anything. ``movie_catalog_metadata`` (migration
0011) made the same call for the same reason and this follows it. The grants
follow from that: ``app_user`` reads, ``admin_user`` writes.

**Point-in-time safety.** Six columns on ``tmdb_movies`` — ``vote_average``,
``vote_count``, ``popularity``, ``budget``, ``revenue`` and ``status`` — are
as-of-pull values. They describe the film as TMDB saw it on the day of the
snapshot, not as it was at any point inside the MovieLens interaction history
that ends in 2019, and there is no timestamp on them that would let a
point-in-time join reconstruct an earlier value. Using one as a ranker feature
against a 2019 temporal split leaks the future into training by construction.
They are stored — they are useful for analysis, for the product's crowd-score
display, and for sanity-checking the snapshot — and they are marked, here, in
the migration, in ``docs/data/tmdb-metadata.md``, and in a unit test that fails
if any of them turns up in ``src/feature_contract.py``.

Everything else is a static attribute of the film: genres, overview, keywords,
cast and crew, runtime, release date, original language, collection membership
and certification do not move once the film exists. Those are the intended
features.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
)

# Its own MetaData, deliberately separate from `src/data/schema.py`'s. That one
# backs `create_all` on a fresh ingest; these tables are created by Alembic and
# must never be swept up by a `metadata.drop_all` during a `--reset` ingest.
tmdb_metadata = MetaData()

# The six as-of-pull columns. Named once, imported by the migration's comment
# text and by the leakage test, so the list cannot drift between the three
# places that have to agree about it.
NOT_POINT_IN_TIME_SAFE_COLUMNS: tuple[str, ...] = (
    "vote_average",
    "vote_count",
    "popularity",
    "budget",
    "revenue",
    "status",
)

# `movie_id` is the primary key rather than `tmdb_id`, which is the shape the
# data forces: 34 TMDB ids in links.csv are claimed by two MovieLens movies
# each. Those are duplicate catalog rows for one film, so `tmdb_id` is indexed
# but not unique, and one fetched payload lands as two rows.
#
# The foreign key to `movies.movieId` is declared in the migration, not here:
# `movies` lives in `src/data/schema.py`'s MetaData and a cross-MetaData string
# reference has nothing to resolve against. The database still enforces it.
tmdb_movies = Table(
    "tmdb_movies",
    tmdb_metadata,
    Column("movie_id", Integer, nullable=False),
    Column("tmdb_id", Integer, nullable=False),
    Column("title", Text, nullable=True),
    Column("original_title", Text, nullable=True),
    Column("overview", Text, nullable=True),
    Column("tagline", Text, nullable=True),
    Column("release_date", Date, nullable=True),
    Column("runtime", Integer, nullable=True),
    Column("original_language", Text, nullable=True),
    Column("adult", Boolean, nullable=True),
    # --- not point-in-time safe: as-of-pull values ---------------------------
    Column("budget", BigInteger, nullable=True),
    Column("revenue", BigInteger, nullable=True),
    Column("status", Text, nullable=True),
    Column("vote_average", Float, nullable=True),
    Column("vote_count", Integer, nullable=True),
    Column("popularity", Float, nullable=True),
    # -------------------------------------------------------------------------
    Column("collection_id", Integer, nullable=True),
    Column("collection_name", Text, nullable=True),
    Column("poster_path", Text, nullable=True),
    Column("backdrop_path", Text, nullable=True),
    Column("imdb_id", Text, nullable=True),
    Column("pulled_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("movie_id", name="pk_tmdb_movies"),
    Index("idx_tmdb_movies_tmdb_id", "tmdb_id"),
    Index("idx_tmdb_movies_collection", "collection_id"),
)

tmdb_movie_genres = Table(
    "tmdb_movie_genres",
    tmdb_metadata,
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("genre_id", Integer, nullable=False),
    Column("genre_name", Text, nullable=False),
    PrimaryKeyConstraint("movie_id", "genre_id", name="pk_tmdb_movie_genres"),
    Index("idx_tmdb_movie_genres_genre", "genre_id"),
)

tmdb_keywords = Table(
    "tmdb_keywords",
    tmdb_metadata,
    Column("keyword_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    PrimaryKeyConstraint("keyword_id", name="pk_tmdb_keywords"),
)

tmdb_movie_keywords = Table(
    "tmdb_movie_keywords",
    tmdb_metadata,
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "keyword_id",
        Integer,
        ForeignKey("tmdb_keywords.keyword_id", ondelete="CASCADE"),
        nullable=False,
    ),
    PrimaryKeyConstraint("movie_id", "keyword_id", name="pk_tmdb_movie_keywords"),
    Index("idx_tmdb_movie_keywords_keyword", "keyword_id"),
)

tmdb_people = Table(
    "tmdb_people",
    tmdb_metadata,
    Column("person_id", Integer, nullable=False),
    Column("name", Text, nullable=True),
    Column("original_name", Text, nullable=True),
    Column("gender", SmallInteger, nullable=True),
    Column("known_for_department", Text, nullable=True),
    Column("profile_path", Text, nullable=True),
    PrimaryKeyConstraint("person_id", name="pk_tmdb_people"),
)

# Keyed on (movie_id, credit_id). `credit_id` is TMDB's identifier for one
# person's involvement in one film, and it has to be in the key because
# (movie_id, person_id) is not unique — the same actor can hold two billed roles
# in one film. `movie_id` has to be in it too, because the 34 duplicated TMDB
# ids put the same credit on two MovieLens movies.
tmdb_movie_cast = Table(
    "tmdb_movie_cast",
    tmdb_metadata,
    Column("credit_id", Text, nullable=False),
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "person_id",
        Integer,
        ForeignKey("tmdb_people.person_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("character", Text, nullable=True),
    Column("cast_order", SmallInteger, nullable=True),
    PrimaryKeyConstraint("movie_id", "credit_id", name="pk_tmdb_movie_cast"),
    Index("idx_tmdb_movie_cast_movie", "movie_id", "cast_order"),
    Index("idx_tmdb_movie_cast_person", "person_id"),
)

tmdb_movie_crew = Table(
    "tmdb_movie_crew",
    tmdb_metadata,
    Column("credit_id", Text, nullable=False),
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "person_id",
        Integer,
        ForeignKey("tmdb_people.person_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("department", Text, nullable=True),
    Column("job", Text, nullable=True),
    PrimaryKeyConstraint("movie_id", "credit_id", name="pk_tmdb_movie_crew"),
    Index("idx_tmdb_movie_crew_movie", "movie_id", "job"),
    Index("idx_tmdb_movie_crew_person", "person_id"),
)

tmdb_production_companies = Table(
    "tmdb_production_companies",
    tmdb_metadata,
    Column("company_id", Integer, nullable=False),
    Column("name", Text, nullable=True),
    Column("origin_country", Text, nullable=True),
    PrimaryKeyConstraint("company_id", name="pk_tmdb_production_companies"),
)

tmdb_movie_production_companies = Table(
    "tmdb_movie_production_companies",
    tmdb_metadata,
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "company_id",
        Integer,
        ForeignKey("tmdb_production_companies.company_id", ondelete="CASCADE"),
        nullable=False,
    ),
    PrimaryKeyConstraint("movie_id", "company_id", name="pk_tmdb_movie_production_companies"),
    Index("idx_tmdb_movie_companies_company", "company_id"),
)

tmdb_production_countries = Table(
    "tmdb_production_countries",
    tmdb_metadata,
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("iso_3166_1", Text, nullable=False),
    Column("name", Text, nullable=True),
    PrimaryKeyConstraint("movie_id", "iso_3166_1", name="pk_tmdb_production_countries"),
)

tmdb_spoken_languages = Table(
    "tmdb_spoken_languages",
    tmdb_metadata,
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("iso_639_1", Text, nullable=False),
    Column("name", Text, nullable=True),
    Column("english_name", Text, nullable=True),
    PrimaryKeyConstraint("movie_id", "iso_639_1", name="pk_tmdb_spoken_languages"),
)

# One row per (country, release type) — a film routinely has a theatrical date
# and a digital date in the same country, and the certification that matters
# (the US rating, say) hangs off a specific one of them. `release_index` breaks
# the tie when TMDB lists two entries of the same type for one country, which it
# does for re-releases.
tmdb_release_dates = Table(
    "tmdb_release_dates",
    tmdb_metadata,
    Column(
        "movie_id",
        Integer,
        ForeignKey("tmdb_movies.movie_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("iso_3166_1", Text, nullable=False),
    Column("release_type", SmallInteger, nullable=False),
    Column("release_index", SmallInteger, nullable=False),
    Column("certification", Text, nullable=True),
    Column("release_date", DateTime(timezone=True), nullable=True),
    Column("note", Text, nullable=True),
    Column("iso_639_1", Text, nullable=True),
    PrimaryKeyConstraint(
        "movie_id", "iso_3166_1", "release_type", "release_index", name="pk_tmdb_release_dates"
    ),
    Index("idx_tmdb_release_dates_country", "iso_3166_1", "certification"),
)

# Load order matters: parents before children, and the reverse on delete.
LOAD_ORDER: tuple[Table, ...] = (
    tmdb_movies,
    tmdb_movie_genres,
    tmdb_keywords,
    tmdb_movie_keywords,
    tmdb_people,
    tmdb_movie_cast,
    tmdb_movie_crew,
    tmdb_production_companies,
    tmdb_movie_production_companies,
    tmdb_production_countries,
    tmdb_spoken_languages,
    tmdb_release_dates,
)
