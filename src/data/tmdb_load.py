"""Normalise the TMDB snapshot into Postgres.

Reads the gzipped JSONL shards `src/data/tmdb_ingest.py` wrote and fans each
payload out across the twelve tables migration ``0018_tmdb_catalog`` creates.
The shards stay the source of truth — this is a derived read model, and dropping
the tables and re-running it costs nothing but time.

Two properties the loader is built around:

**Idempotent.** A movie's rows are deleted and re-inserted as a unit, so a
re-run over the same shards leaves the database in the same state, and a re-run
over a *newer* snapshot replaces what the old one said rather than accumulating
both. The three dimension tables — keywords, people, production companies — are
upserted instead, because they are shared across movies and a delete keyed on
one movie would take another movie's row with it.

**Streaming.** The catalog is 62k payloads and some of them are large; the
loader holds one batch at a time rather than the snapshot.

`alternative_titles` and `translations` are pulled and kept in the snapshot but
are not normalised into tables here. Nothing consumes them yet, and a table
nothing reads is a table that rots — they are in the shards the day something
needs them, which is the whole reason for storing the payload verbatim.

    make db-migrate            # 0018 creates the tables
    make tmdb-load             # loads the newest snapshot under data/raw/tmdb/
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Table, create_engine, delete
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.config import Settings
from src.data.tmdb_ingest import read_shard_records
from src.data.tmdb_schema import (
    tmdb_keywords,
    tmdb_movie_cast,
    tmdb_movie_crew,
    tmdb_movie_genres,
    tmdb_movie_keywords,
    tmdb_movie_production_companies,
    tmdb_movies,
    tmdb_people,
    tmdb_production_companies,
    tmdb_production_countries,
    tmdb_release_dates,
    tmdb_spoken_languages,
)

logger = logging.getLogger(__name__)

DEFAULT_TOP_CAST = 15
DEFAULT_BATCH_SIZE = 500

# The tables a movie owns outright, cleared and rewritten together. The three
# dimension tables (people, keywords, companies) are missing from this list on
# purpose: they are shared, so a delete keyed on one movie would take another
# movie's row with it.
MOVIE_SCOPED_TABLES: tuple[Table, ...] = (
    tmdb_movie_genres,
    tmdb_movie_keywords,
    tmdb_movie_cast,
    tmdb_movie_crew,
    tmdb_movie_production_companies,
    tmdb_production_countries,
    tmdb_spoken_languages,
    tmdb_release_dates,
)

# The crew roles worth a row. TMDB's crew list runs to a hundred people on a
# studio film — every gaffer, every assistant editor — and almost none of them
# say anything about whether a viewer will like the film. These are the ones a
# person would name if you asked them who made it, which is also the set that
# behaves like a taste signal: a director or a composer recurs across a
# filmography in a way a boom operator does not. `--all-crew` keeps everything
# for anyone who wants to test that judgement rather than take it.
CREW_JOBS: frozenset[str] = frozenset(
    {
        "Director",
        "Co-Director",
        "Writer",
        "Screenplay",
        "Story",
        "Novel",
        "Author",
        "Characters",
        "Producer",
        "Executive Producer",
        "Co-Producer",
        "Original Music Composer",
        "Music",
        "Director of Photography",
        "Cinematography",
        "Editor",
        "Production Design",
        "Costume Design",
    }
)


@dataclass
class MovieRows:
    """Every row one payload produces, grouped by the table it belongs in."""

    movie: dict[str, Any]
    genres: list[dict[str, Any]] = field(default_factory=list)
    keywords: list[dict[str, Any]] = field(default_factory=list)
    movie_keywords: list[dict[str, Any]] = field(default_factory=list)
    people: list[dict[str, Any]] = field(default_factory=list)
    cast: list[dict[str, Any]] = field(default_factory=list)
    crew: list[dict[str, Any]] = field(default_factory=list)
    companies: list[dict[str, Any]] = field(default_factory=list)
    movie_companies: list[dict[str, Any]] = field(default_factory=list)
    countries: list[dict[str, Any]] = field(default_factory=list)
    languages: list[dict[str, Any]] = field(default_factory=list)
    release_dates: list[dict[str, Any]] = field(default_factory=list)


def normalise_record(
    record: dict[str, Any],
    *,
    top_cast: int = DEFAULT_TOP_CAST,
    crew_jobs: frozenset[str] | None = CREW_JOBS,
) -> list[MovieRows]:
    """Turn one shard record into rows, one set per MovieLens movie it covers.

    Returns an empty list for a 404 record — TMDB genuinely has nothing for that
    id, and a row of nulls would be indistinguishable from a film whose fields
    are simply blank.
    """
    if record.get("status") != "ok":
        return []
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return []

    movie_ids = [mid for mid in record.get("movie_ids", []) if isinstance(mid, int)]
    if not movie_ids:
        return []
    pulled_at = _as_datetime(record.get("fetched_at"))

    collection = payload.get("belongs_to_collection")
    collection_id = _as_int(collection.get("id")) if isinstance(collection, dict) else None
    collection_name = _as_text(collection.get("name")) if isinstance(collection, dict) else None

    external = payload.get("external_ids")
    imdb_id = _as_text(payload.get("imdb_id"))
    if imdb_id is None and isinstance(external, dict):
        imdb_id = _as_text(external.get("imdb_id"))

    tmdb_id = _as_int(record.get("tmdb_id")) or _as_int(payload.get("id"))

    genres = _dedupe(
        (
            {"genre_id": gid, "genre_name": name}
            for gid, name in _id_name_pairs(payload.get("genres"))
        ),
        key="genre_id",
    )
    keyword_rows = _dedupe(
        (
            {"keyword_id": kid, "name": name}
            for kid, name in _id_name_pairs(_nested_list(payload, "keywords", "keywords"))
        ),
        key="keyword_id",
    )
    company_rows = _dedupe(
        (
            {
                "company_id": cid,
                "name": _as_text(entry.get("name")),
                "origin_country": _as_text(entry.get("origin_country")),
            }
            for entry, cid in _entries_with_id(payload.get("production_companies"))
        ),
        key="company_id",
    )

    raw_credits = payload.get("credits")
    credits: dict[str, Any] = raw_credits if isinstance(raw_credits, dict) else {}
    cast_entries = _top_cast(credits.get("cast"), top_cast)
    crew_entries = _selected_crew(credits.get("crew"), crew_jobs)
    people_rows = _dedupe(
        (_person_row(entry) for entry in [*cast_entries, *crew_entries]),
        key="person_id",
    )

    rows: list[MovieRows] = []
    for movie_id in movie_ids:
        out = MovieRows(
            movie={
                "movie_id": movie_id,
                "tmdb_id": tmdb_id,
                "title": _as_text(payload.get("title")),
                "original_title": _as_text(payload.get("original_title")),
                "overview": _as_text(payload.get("overview")),
                "tagline": _as_text(payload.get("tagline")),
                "release_date": _as_date(payload.get("release_date")),
                "runtime": _as_int(payload.get("runtime")),
                "original_language": _as_text(payload.get("original_language")),
                "adult": _as_bool(payload.get("adult")),
                # not point-in-time safe: as-of-pull values. Stored, never a feature.
                "budget": _as_int(payload.get("budget")),
                "revenue": _as_int(payload.get("revenue")),
                "status": _as_text(payload.get("status")),
                "vote_average": _as_float(payload.get("vote_average")),
                "vote_count": _as_int(payload.get("vote_count")),
                "popularity": _as_float(payload.get("popularity")),
                "collection_id": collection_id,
                "collection_name": collection_name,
                "poster_path": _as_text(payload.get("poster_path")),
                "backdrop_path": _as_text(payload.get("backdrop_path")),
                "imdb_id": imdb_id,
                "pulled_at": pulled_at,
            },
            genres=[{"movie_id": movie_id, **row} for row in genres],
            keywords=list(keyword_rows),
            movie_keywords=[
                {"movie_id": movie_id, "keyword_id": row["keyword_id"]} for row in keyword_rows
            ],
            people=list(people_rows),
            companies=list(company_rows),
            movie_companies=[
                {"movie_id": movie_id, "company_id": row["company_id"]} for row in company_rows
            ],
        )
        out.cast = _dedupe(
            (
                {
                    "movie_id": movie_id,
                    "credit_id": credit_id,
                    "person_id": person_id,
                    "character": _as_text(entry.get("character")),
                    "cast_order": _as_int(entry.get("order")),
                }
                for entry, credit_id, person_id in _credit_entries(cast_entries)
            ),
            key="credit_id",
        )
        out.crew = _dedupe(
            (
                {
                    "movie_id": movie_id,
                    "credit_id": credit_id,
                    "person_id": person_id,
                    "department": _as_text(entry.get("department")),
                    "job": _as_text(entry.get("job")),
                }
                for entry, credit_id, person_id in _credit_entries(crew_entries)
            ),
            key="credit_id",
        )
        out.countries = _dedupe(
            (
                {
                    "movie_id": movie_id,
                    "iso_3166_1": code,
                    "name": _as_text(entry.get("name")),
                }
                for entry, code in _coded_entries(payload.get("production_countries"), "iso_3166_1")
            ),
            key="iso_3166_1",
        )
        out.languages = _dedupe(
            (
                {
                    "movie_id": movie_id,
                    "iso_639_1": code,
                    "name": _as_text(entry.get("name")),
                    "english_name": _as_text(entry.get("english_name")),
                }
                for entry, code in _coded_entries(payload.get("spoken_languages"), "iso_639_1")
            ),
            key="iso_639_1",
        )
        out.release_dates = _release_date_rows(payload, movie_id)
        rows.append(out)
    return rows


def _release_date_rows(payload: dict[str, Any], movie_id: int) -> list[dict[str, Any]]:
    """Flatten ``release_dates.results`` into one row per (country, type, index).

    ``release_index`` exists because TMDB will list two entries of the same type
    for one country — a re-release, usually — and without it the second silently
    overwrites the first.
    """
    rows: list[dict[str, Any]] = []
    for group in _as_list(_nested(payload, "release_dates", "results")):
        if not isinstance(group, dict):
            continue
        country = _as_text(group.get("iso_3166_1"))
        if country is None:
            continue
        seen_types: dict[int, int] = {}
        for entry in _as_list(group.get("release_dates")):
            if not isinstance(entry, dict):
                continue
            release_type = _as_int(entry.get("type"))
            if release_type is None:
                continue
            index = seen_types.get(release_type, 0)
            seen_types[release_type] = index + 1
            rows.append(
                {
                    "movie_id": movie_id,
                    "iso_3166_1": country,
                    "release_type": release_type,
                    "release_index": index,
                    "certification": _as_text(entry.get("certification")),
                    "release_date": _as_datetime(entry.get("release_date")),
                    "note": _as_text(entry.get("note")),
                    "iso_639_1": _as_text(entry.get("iso_639_1")),
                }
            )
    return rows


def _person_row(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "person_id": _as_int(entry.get("id")),
        "name": _as_text(entry.get("name")),
        "original_name": _as_text(entry.get("original_name")),
        "gender": _as_int(entry.get("gender")),
        "known_for_department": _as_text(entry.get("known_for_department")),
        "profile_path": _as_text(entry.get("profile_path")),
    }


def _top_cast(cast: Any, limit: int) -> list[dict[str, Any]]:
    """The first ``limit`` billed roles, ordered by TMDB's own ``order`` field.

    Sorted rather than trusted: the API returns them in order today, and a slice
    that silently depends on that is a slice that breaks quietly.
    """
    entries = [entry for entry in _as_list(cast) if isinstance(entry, dict)]
    entries.sort(
        key=lambda entry: (_as_int(entry.get("order")) is None, _as_int(entry.get("order")) or 0)
    )
    return entries[:limit]


def _selected_crew(crew: Any, crew_jobs: frozenset[str] | None) -> list[dict[str, Any]]:
    entries = [entry for entry in _as_list(crew) if isinstance(entry, dict)]
    if crew_jobs is None:
        return entries
    return [entry for entry in entries if _as_text(entry.get("job")) in crew_jobs]


def _credit_entries(
    entries: Iterable[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], str, int]]:
    for entry in entries:
        credit_id = _as_text(entry.get("credit_id"))
        person_id = _as_int(entry.get("id"))
        if credit_id is None or person_id is None:
            continue
        yield entry, credit_id, person_id


def _id_name_pairs(value: Any) -> Iterator[tuple[int, str]]:
    for entry in _as_list(value):
        if not isinstance(entry, dict):
            continue
        entry_id = _as_int(entry.get("id"))
        name = _as_text(entry.get("name"))
        if entry_id is None or name is None:
            continue
        yield entry_id, name


def _entries_with_id(value: Any) -> Iterator[tuple[dict[str, Any], int]]:
    for entry in _as_list(value):
        if not isinstance(entry, dict):
            continue
        entry_id = _as_int(entry.get("id"))
        if entry_id is None:
            continue
        yield entry, entry_id


def _coded_entries(value: Any, code_field: str) -> Iterator[tuple[dict[str, Any], str]]:
    for entry in _as_list(value):
        if not isinstance(entry, dict):
            continue
        code = _as_text(entry.get(code_field))
        if code is None:
            continue
        yield entry, code


def _dedupe(rows: Iterable[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    """Last write wins, order preserved.

    Needed before the insert rather than after: Postgres refuses an ``ON
    CONFLICT`` statement that would touch the same row twice in one command, so
    a payload listing a keyword twice would fail the whole batch.
    """
    seen: dict[Any, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        seen[value] = row
    return list(seen.values())


# --- payload coercion -------------------------------------------------------
# TMDB is loose about empty values: a missing release date is "", a missing
# runtime is sometimes 0 and sometimes null, a missing tagline is "". Coercing
# in one place keeps that knowledge out of the twelve call sites.


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _nested(payload: dict[str, Any], *path: str) -> Any:
    current: Any = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _nested_list(payload: dict[str, Any], *path: str) -> list[Any]:
    return _as_list(_nested(payload, *path))


def _as_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_date(value: Any) -> date | None:
    text = _as_text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime | None:
    text = _as_text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


# --- the load ---------------------------------------------------------------


@dataclass
class LoadStats:
    records_read: int = 0
    not_found_skipped: int = 0
    movies_loaded: int = 0
    cast_rows: int = 0
    crew_rows: int = 0
    keyword_rows: int = 0


def load_snapshot(
    engine: Engine,
    snapshot_dir: Path,
    *,
    top_cast: int = DEFAULT_TOP_CAST,
    crew_jobs: frozenset[str] | None = CREW_JOBS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = 10_000,
) -> LoadStats:
    """Load every shard in ``snapshot_dir`` into the normalised tables."""
    stats = LoadStats()
    batch: list[MovieRows] = []
    for record in read_shard_records(snapshot_dir):
        stats.records_read += 1
        rows = normalise_record(record, top_cast=top_cast, crew_jobs=crew_jobs)
        if not rows:
            stats.not_found_skipped += 1
            continue
        batch.extend(rows)
        if len(batch) >= batch_size:
            _write_batch(engine, batch, stats)
            batch = []
        if progress_every and stats.records_read % progress_every == 0:
            logger.info(
                "%d records read, %d movies loaded", stats.records_read, stats.movies_loaded
            )
    if batch:
        _write_batch(engine, batch, stats)
    return stats


def _write_batch(engine: Engine, batch: Sequence[MovieRows], stats: LoadStats) -> None:
    """One transaction per batch: replace these movies, upsert the dimensions.

    Replacing rather than upserting the movie-scoped rows is what makes a
    re-load over a newer snapshot correct — a film that lost a keyword between
    two pulls should lose the row, and an upsert would leave it behind forever.
    """
    dialect = engine.dialect.name
    movie_ids = [rows.movie["movie_id"] for rows in batch]

    with engine.begin() as connection:
        # Dimensions first: the credit and keyword rows reference them.
        _upsert(
            connection_execute=connection.execute,
            table=tmdb_people,
            rows=_merge(batch, "people", key="person_id"),
            key=("person_id",),
            dialect=dialect,
        )
        _upsert(
            connection_execute=connection.execute,
            table=tmdb_keywords,
            rows=_merge(batch, "keywords", key="keyword_id"),
            key=("keyword_id",),
            dialect=dialect,
        )
        _upsert(
            connection_execute=connection.execute,
            table=tmdb_production_companies,
            rows=_merge(batch, "companies", key="company_id"),
            key=("company_id",),
            dialect=dialect,
        )

        # Children first, then the parent, and the inserts in the reverse
        # order. Postgres' ON DELETE CASCADE would take the children anyway,
        # but the loader must not depend on it: SQLite enforces no foreign key
        # unless `PRAGMA foreign_keys` is on, so a cascade-only delete silently
        # leaves orphans on the dialect the unit tests use — which is exactly
        # the shape of bug that only shows up on the second load.
        execute = connection.execute
        for table in MOVIE_SCOPED_TABLES:
            execute(delete(table).where(table.c.movie_id.in_(movie_ids)))
        execute(delete(tmdb_movies).where(tmdb_movies.c.movie_id.in_(movie_ids)))

        _insert(execute, tmdb_movies, [rows.movie for rows in batch])
        _insert(execute, tmdb_movie_genres, _concat(batch, "genres"))
        _insert(execute, tmdb_movie_keywords, _concat(batch, "movie_keywords"))
        _insert(execute, tmdb_movie_cast, _concat(batch, "cast"))
        _insert(execute, tmdb_movie_crew, _concat(batch, "crew"))
        _insert(execute, tmdb_movie_production_companies, _concat(batch, "movie_companies"))
        _insert(execute, tmdb_production_countries, _concat(batch, "countries"))
        _insert(execute, tmdb_spoken_languages, _concat(batch, "languages"))
        _insert(execute, tmdb_release_dates, _concat(batch, "release_dates"))

    stats.movies_loaded += len(batch)
    stats.cast_rows += sum(len(rows.cast) for rows in batch)
    stats.crew_rows += sum(len(rows.crew) for rows in batch)
    stats.keyword_rows += sum(len(rows.movie_keywords) for rows in batch)


def _concat(batch: Sequence[MovieRows], attribute: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rows in batch:
        out.extend(getattr(rows, attribute))
    return out


def _merge(batch: Sequence[MovieRows], attribute: str, *, key: str) -> list[dict[str, Any]]:
    return _dedupe(_concat(batch, attribute), key=key)


def _insert(connection_execute: Any, table: Table, rows: Sequence[dict[str, Any]]) -> None:
    if rows:
        connection_execute(table.insert(), list(rows))


def _upsert(
    *,
    connection_execute: Any,
    table: Table,
    rows: Sequence[dict[str, Any]],
    key: tuple[str, ...],
    dialect: str,
) -> None:
    """Insert-or-refresh for the shared dimension tables.

    Refresh rather than ignore: a later snapshot may carry a corrected name or a
    new profile path, and a dimension row that can never be updated makes the
    tables disagree with the shards they came from.
    """
    if not rows:
        return
    if dialect == "postgresql":
        statement = postgres_insert(table).values(list(rows))
        updates = {
            column.name: statement.excluded[column.name]
            for column in table.columns
            if column.name not in key
        }
        connection_execute(statement.on_conflict_do_update(index_elements=list(key), set_=updates))
        return
    if dialect == "sqlite":
        sqlite_statement = sqlite_insert(table).values(list(rows))
        sqlite_updates = {
            column.name: sqlite_statement.excluded[column.name]
            for column in table.columns
            if column.name not in key
        }
        connection_execute(
            sqlite_statement.on_conflict_do_update(index_elements=list(key), set_=sqlite_updates)
        )
        return
    connection_execute(table.insert(), list(rows))


def newest_snapshot_dir(settings: Settings) -> Path | None:
    """The most recent dated directory under ``data/raw/tmdb/``."""
    root = settings.raw_data_dir / "tmdb"
    if not root.is_dir():
        return None
    candidates = sorted((path for path in root.iterdir() if path.is_dir()), reverse=True)
    return candidates[0] if candidates else None


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Load a TMDB snapshot into the normalised catalog tables.",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=None,
        help="Snapshot directory (default: the newest under <raw_data_dir>/tmdb/).",
    )
    parser.add_argument("--top-cast", type=int, default=DEFAULT_TOP_CAST)
    parser.add_argument(
        "--all-crew",
        action="store_true",
        help="Store every crew member rather than the named roles.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)

    settings = Settings()
    snapshot_dir = args.snapshot_dir or newest_snapshot_dir(settings)
    if snapshot_dir is None or not snapshot_dir.is_dir():
        raise SystemExit("No TMDB snapshot found. Run `make tmdb-ingest` (or `dvc pull`) first.")

    engine = create_engine(settings.database_url)
    logger.info("Loading %s", snapshot_dir)
    stats = load_snapshot(
        engine,
        snapshot_dir,
        top_cast=args.top_cast,
        crew_jobs=None if args.all_crew else CREW_JOBS,
        batch_size=args.batch_size,
    )
    logger.info(
        "Loaded %d movies from %d records (%d had no TMDB entry): "
        "%d cast, %d crew, %d keyword links",
        stats.movies_loaded,
        stats.records_read,
        stats.not_found_skipped,
        stats.cast_rows,
        stats.crew_rows,
        stats.keyword_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
