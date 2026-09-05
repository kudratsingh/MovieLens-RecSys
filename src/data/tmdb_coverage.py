"""Measure what the TMDB snapshot actually covers.

ADR 0017's first increment failed on its own terms: genre-and-year similarity
reached 4,998 cold items and scored 0.0001 recall doing it, and 3,413 items have
no MovieLens genres at all and cannot be represented by that increment even in
principle. Increment 2 is TMDB metadata, and before anything is built on it the
question that has to be answered is how much of the population it actually
reaches. That number is what this module produces.

Three coverage questions, in ascending order of how much they matter:

1. **The catalog.** How many of the 62,423 MovieLens movies have a ``tmdbId``,
   how many of those TMDB resolved, and how many carry each field a content
   representation would use — an overview, keywords, a cast.
2. **The cold items.** The 27,962 movies with no interaction in the ADR 0001
   training frame. These are the population the whole rung exists for, and the
   genome covers 0.00% of them, so their TMDB coverage is the number that says
   whether increment 2 is worth building.
3. **The items with no MovieLens genres.** 3,413 of them, unreachable by
   increment 1 by construction. TMDB coverage here is the *only* way they become
   representable at all.

Counted, never assumed, and separated by field: a movie whose TMDB entry exists
but carries an empty overview and no keywords is resolved and still useless to a
text representation, and a report that collapsed those into one "covered" number
would hide exactly the thing the first increment got wrong.

    make tmdb-coverage                     # writes docs/data/tmdb-coverage.md
    make tmdb-coverage ARGS="--no-cold"    # catalog only, no ratings scan
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

from src.config import Settings
from src.data.split import temporal_split
from src.data.tmdb_ingest import MANIFEST_NAME, read_shard_records
from src.data.tmdb_load import DEFAULT_TOP_CAST, normalise_record

logger = logging.getLogger(__name__)

NO_GENRES = "(no genres listed)"


@dataclass
class FieldCoverage:
    """How many movies in one population carry each field worth having."""

    population: int = 0
    with_tmdb_id: int = 0
    resolved: int = 0
    not_found: int = 0
    missing_from_snapshot: int = 0
    with_overview: int = 0
    with_keywords: int = 0
    with_cast: int = 0
    with_crew: int = 0
    with_genres: int = 0
    with_runtime: int = 0
    with_release_date: int = 0
    with_collection: int = 0
    with_certification: int = 0

    def observe(self, facts: MovieFacts) -> None:
        self.population += 1
        if not facts.has_tmdb_id:
            return
        self.with_tmdb_id += 1
        if facts.status == "not_found":
            self.not_found += 1
            return
        if facts.status != "ok":
            self.missing_from_snapshot += 1
            return
        self.resolved += 1
        self.with_overview += int(facts.has_overview)
        self.with_keywords += int(facts.has_keywords)
        self.with_cast += int(facts.has_cast)
        self.with_crew += int(facts.has_crew)
        self.with_genres += int(facts.has_genres)
        self.with_runtime += int(facts.has_runtime)
        self.with_release_date += int(facts.has_release_date)
        self.with_collection += int(facts.has_collection)
        self.with_certification += int(facts.has_certification)


@dataclass(frozen=True)
class MovieFacts:
    """What the snapshot knows about one MovieLens movie, reduced to booleans."""

    movie_id: int
    has_tmdb_id: bool
    status: str  # "ok" | "not_found" | "absent"
    has_overview: bool = False
    has_keywords: bool = False
    has_cast: bool = False
    has_crew: bool = False
    has_genres: bool = False
    has_runtime: bool = False
    has_release_date: bool = False
    has_collection: bool = False
    has_certification: bool = False


@dataclass
class CoverageReport:
    snapshot_dir: str
    pull_date: str | None
    catalog: FieldCoverage = field(default_factory=FieldCoverage)
    cold_items: FieldCoverage | None = None
    no_movielens_genres: FieldCoverage | None = None
    manifest: dict[str, Any] = field(default_factory=dict)


def read_snapshot_facts(
    snapshot_dir: Path, *, top_cast: int = DEFAULT_TOP_CAST
) -> dict[int, MovieFacts]:
    """Reduce every shard record to per-movie booleans.

    Reduced during the scan rather than after it: the payloads are the whole
    snapshot and holding them to answer nine yes/no questions would cost
    gigabytes for no reason.
    """
    facts: dict[int, MovieFacts] = {}
    for record in read_shard_records(snapshot_dir):
        movie_ids = [mid for mid in record.get("movie_ids", []) if isinstance(mid, int)]
        status = record.get("status")
        if status != "ok":
            for movie_id in movie_ids:
                facts[movie_id] = MovieFacts(
                    movie_id=movie_id, has_tmdb_id=True, status="not_found"
                )
            continue
        # Reuse the loader's normaliser rather than re-reading the payload's
        # shape here. If the two ever disagreed, the report would describe a
        # database that does not exist.
        for rows in normalise_record(record, top_cast=top_cast):
            movie_id = int(rows.movie["movie_id"])
            facts[movie_id] = MovieFacts(
                movie_id=movie_id,
                has_tmdb_id=True,
                status="ok",
                has_overview=bool(rows.movie.get("overview")),
                has_keywords=bool(rows.movie_keywords),
                has_cast=bool(rows.cast),
                has_crew=bool(rows.crew),
                has_genres=bool(rows.genres),
                has_runtime=rows.movie.get("runtime") is not None,
                has_release_date=rows.movie.get("release_date") is not None,
                has_collection=rows.movie.get("collection_id") is not None,
                has_certification=any(row.get("certification") for row in rows.release_dates),
            )
    return facts


def read_catalog(links_csv: Path, movies_csv: Path) -> tuple[dict[int, bool], set[int]]:
    """Return (movie_id -> has a tmdbId) and the set with no MovieLens genres."""
    has_tmdb: dict[int, bool] = {}
    with links_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                movie_id = int(row["movieId"])
            except (KeyError, TypeError, ValueError):
                continue
            has_tmdb[movie_id] = bool((row.get("tmdbId") or "").strip())

    without_genres: set[int] = set()
    with movies_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                movie_id = int(row["movieId"])
            except (KeyError, TypeError, ValueError):
                continue
            genres = (row.get("genres") or "").strip()
            if not genres or genres == NO_GENRES:
                without_genres.add(movie_id)
    return has_tmdb, without_genres


def cold_item_ids(database_url: str) -> set[int]:
    """Catalog movies with no interaction in the ADR 0001 training frame.

    Computed from the same ``temporal_split`` every trainer uses rather than
    from a hardcoded id list, so the report cannot quietly describe a different
    population than the models do. Only two columns are read out of Postgres —
    a full 25M-row ratings frame is not needed to answer "which ids appear
    before the cutoff".
    """
    engine = create_engine(database_url)
    ratings = pd.read_sql('SELECT "movieId", timestamp FROM ratings', engine)
    train_items = set(temporal_split(ratings).train["movieId"].unique().tolist())
    with engine.connect() as connection:
        catalog = {row[0] for row in connection.execute(text('SELECT "movieId" FROM movies'))}
    return catalog - train_items


def build_report(
    *,
    snapshot_dir: Path,
    links_csv: Path,
    movies_csv: Path,
    cold_items: set[int] | None,
) -> CoverageReport:
    has_tmdb, without_genres = read_catalog(links_csv, movies_csv)
    facts = read_snapshot_facts(snapshot_dir)

    manifest_path = snapshot_dir / MANIFEST_NAME
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            manifest = loaded

    report = CoverageReport(
        snapshot_dir=str(snapshot_dir),
        pull_date=manifest.get("pull_date"),
        cold_items=FieldCoverage() if cold_items is not None else None,
        no_movielens_genres=FieldCoverage(),
        manifest={
            key: manifest.get(key)
            for key in ("pull_date", "api_version", "append_to_response", "records", "runs")
            if key in manifest
        },
    )

    for movie_id, linked in has_tmdb.items():
        observed = facts.get(movie_id)
        if observed is None:
            observed = MovieFacts(
                movie_id=movie_id,
                has_tmdb_id=linked,
                status="absent" if linked else "absent",
            )
        report.catalog.observe(observed)
        if report.cold_items is not None and cold_items is not None and movie_id in cold_items:
            report.cold_items.observe(observed)
        if report.no_movielens_genres is not None and movie_id in without_genres:
            report.no_movielens_genres.observe(observed)
    return report


def _percent(numerator: int, denominator: int) -> str:
    return "—" if denominator == 0 else f"{100.0 * numerator / denominator:.1f}%"


# Label per field, in the order the report reads best: the resolution funnel
# first, then what each resolved payload actually carries.
_ROW_LABELS: tuple[tuple[str, str], ...] = (
    ("with_tmdb_id", "Has a `tmdbId` in links.csv"),
    ("resolved", "Resolved by TMDB"),
    ("not_found", "TMDB answered 404"),
    ("missing_from_snapshot", "Not in the snapshot (failed or not yet fetched)"),
    ("with_overview", "— with an overview"),
    ("with_keywords", "— with keywords"),
    ("with_cast", "— with cast"),
    ("with_crew", "— with crew (selected roles)"),
    ("with_genres", "— with TMDB genres"),
    ("with_runtime", "— with a runtime"),
    ("with_release_date", "— with a release date"),
    ("with_collection", "— in a collection"),
    ("with_certification", "— with a certification"),
)


def _coverage_rows(coverage: FieldCoverage) -> list[tuple[str, int, str]]:
    rows = [("Population", coverage.population, "100.0%")]
    for attribute, label in _ROW_LABELS:
        value = int(getattr(coverage, attribute))
        rows.append((label, value, _percent(value, coverage.population)))
    return rows


def render_table(title: str, coverage: FieldCoverage) -> str:
    lines = [f"### {title}", "", "| | Movies | Share |", "|---|---:|---:|"]
    for label, value, share in _coverage_rows(coverage):
        lines.append(f"| {label} | {value:,} | {share} |")
    lines.append("")
    return "\n".join(lines)


def render_markdown(report: CoverageReport) -> str:
    parts = [
        "<!-- Generated by `make tmdb-coverage`. Numbers only; the narrative "
        "around them lives in docs/data/tmdb-metadata.md above this block. -->",
        "",
        f"**Snapshot:** `{report.snapshot_dir}`  ",
        f"**Pull date:** {report.pull_date or 'unknown'}",
        "",
        render_table("Whole catalog", report.catalog),
    ]
    if report.cold_items is not None:
        parts.append(
            render_table(
                "Cold items (no interaction in the ADR 0001 train frame)", report.cold_items
            )
        )
    if report.no_movielens_genres is not None:
        parts.append(render_table("Items with no MovieLens genres", report.no_movielens_genres))
    return "\n".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Measure TMDB snapshot coverage.")
    parser.add_argument("--snapshot-dir", type=Path, default=None)
    parser.add_argument("--links-csv", type=Path, default=None)
    parser.add_argument("--movies-csv", type=Path, default=None)
    parser.add_argument(
        "--no-cold",
        action="store_true",
        help="Skip the cold-item slice, which needs a ratings scan out of Postgres.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Write the markdown here.")
    parser.add_argument("--json-out", type=Path, default=None, help="Also write the raw counts.")
    args = parser.parse_args(argv)

    settings = Settings()
    from src.data.tmdb_load import newest_snapshot_dir

    snapshot_dir = args.snapshot_dir or newest_snapshot_dir(settings)
    if snapshot_dir is None or not snapshot_dir.is_dir():
        raise SystemExit("No TMDB snapshot found. Run `make tmdb-ingest` first.")

    links_csv = args.links_csv or (settings.raw_data_dir / "ml-25m" / "links.csv")
    movies_csv = args.movies_csv or (settings.raw_data_dir / "ml-25m" / "movies.csv")
    for path in (links_csv, movies_csv):
        if not path.exists():
            raise SystemExit(f"{path} not found — run `make data-download` (or `dvc pull`) first.")

    cold = None if args.no_cold else cold_item_ids(settings.database_url)
    report = build_report(
        snapshot_dir=snapshot_dir,
        links_csv=links_csv,
        movies_csv=movies_csv,
        cold_items=cold,
    )

    markdown = render_markdown(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown + "\n", encoding="utf-8")
        logger.info("Wrote %s", args.out)
    else:
        print(markdown)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps({**asdict(report), "generated": date.today().isoformat()}, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Wrote %s", args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
