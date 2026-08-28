"""Offline poster enrichment for the reviewed demo catalog fixture.

The product renders posters from ``movie_catalog_metadata``, which is seeded
from ``synthetic/personas/catalog.json`` and never fanned out to TMDB per card
(``docs/frontend/catalog-contract.md``). A title without a ``poster_url`` in the
fixture is therefore a title that shows a placeholder in Discover, Browse, the
detail page and Quick Picks forever, no matter what TMDB knows about it. This
script is the offline enrichment step that keeps the fixture and TMDB in sync.

Re-run it when the fixture gains titles, or when a poster path has gone stale
upstream::

    set -a && . /path/to/tmdb.env && set +a     # TMDB_READ_ACCESS_TOKEN
    python -m synthetic.personas.enrich_posters --dry-run
    python -m synthetic.personas.enrich_posters

The token is read from ``TMDB_READ_ACCESS_TOKEN`` and from nowhere else — it is
never written into the repository, and the FastAPI service does not need it for
the catalog path. Default behaviour fills only the entries that have no
``poster_url``; ``--refresh`` re-fetches every entry and overwrites. Both modes
are idempotent: a second run over an already-enriched fixture changes no bytes.

Entries with no ``tmdb_id`` are resolved by a year-constrained title search and
the resolved id is written back, so the next run is a plain id lookup. Matches
that were not exact on title are printed under "review" — the fixture is a
*reviewed* snapshot (``metadata_source = 'reviewed-fixture'``), so a fuzzy match
is a thing a human is expected to glance at rather than something the script
should quietly bless.

Attribution: the TMDB logo and the "not endorsed or certified by TMDB" notice
are rendered by the web app in ``web/components/legacy/recommendation-demo.tsx``
(and poster hosts are pinned to ``image.tmdb.org/t/p/w500`` in
``web/next.config.ts`` and ``web/components/quick-picks/quick-picks-deck.tsx``),
so enriching the fixture adds no new attribution obligation here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Same endpoints, poster size and URL shape the serving client uses
# (``src/serving/tmdb.py``), so a fixture-seeded poster and a live-fetched one
# are byte-identical strings.
API_BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"

CATALOG_PATH = Path(__file__).parent / "catalog.json"

REQUEST_TIMEOUT_SECONDS = 10.0
# Roughly four requests a second. TMDB's published limits are far looser, but a
# fixture of a few hundred titles has no reason to go faster than a human would.
MIN_REQUEST_INTERVAL_SECONDS = 0.25
MAX_RETRY_WAIT_SECONDS = 30.0

# The order enriched entries have always been written in. New fields are slotted
# into it rather than appended, so the 24 hand-reviewed rows keep their exact
# byte layout and the diff shows only what this run actually added.
CANONICAL_FIELD_ORDER = (
    "movie_id",
    "title",
    "genres",
    "tmdb_id",
    "release_year",
    "poster_url",
    "overview",
)


class CatalogFormatError(RuntimeError):
    """The fixture is not in the one-object-per-line shape this script edits."""


class TmdbRequestError(RuntimeError):
    """A TMDB request failed after its single retry."""


@dataclass
class CatalogEntry:
    """One movie object, bound to the source line it was parsed from."""

    line_index: int
    trailing_comma: bool
    data: dict[str, Any]

    @property
    def movie_id(self) -> int:
        return int(self.data["movie_id"])

    @property
    def title(self) -> str:
        return str(self.data["title"])

    @property
    def tmdb_id(self) -> str | None:
        value = self.data.get("tmdb_id")
        return str(value) if value is not None else None

    @property
    def poster_url(self) -> str | None:
        value = self.data.get("poster_url")
        return str(value) if value is not None else None

    def set_fields(self, **fields: str) -> None:
        """Apply fields and restore the canonical key order."""
        merged = dict(self.data)
        merged.update(fields)
        ordered = {key: merged[key] for key in CANONICAL_FIELD_ORDER if key in merged}
        # Anything the fixture grows later that this script does not know about
        # keeps its relative position at the end rather than being dropped.
        ordered.update({key: value for key, value in merged.items() if key not in ordered})
        self.data = ordered

    def render(self) -> str:
        return "    " + json.dumps(self.data) + ("," if self.trailing_comma else "")


@dataclass
class CatalogDocument:
    """The fixture as lines plus parsed entries, so writes stay surgical.

    Rendering the whole file from the parsed JSON would drop the blank line that
    separates the originally-enriched block from the rest, and would silently
    reformat anything a future editor writes by hand. Editing the lines the
    entries came from keeps the diff to the fields this script changed.
    """

    lines: list[str]
    entries: list[CatalogEntry]
    trailing_newline: bool

    def render(self) -> str:
        lines = list(self.lines)
        for entry in self.entries:
            lines[entry.line_index] = entry.render()
        return "\n".join(lines) + ("\n" if self.trailing_newline else "")


def parse_catalog(text: str) -> CatalogDocument:
    """Parse the fixture and prove it round-trips byte-for-byte.

    The round-trip assertion is the safety net for the surgical write: if an
    untouched entry does not re-render to the exact line it came from, the
    formatting assumption is wrong and this script must not rewrite the file.
    """
    trailing_newline = text.endswith("\n")
    lines = text[:-1].split("\n") if trailing_newline else text.split("\n")
    entries: list[CatalogEntry] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('{"movie_id"'):
            continue
        trailing_comma = stripped.endswith(",")
        body = stripped[:-1] if trailing_comma else stripped
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:  # pragma: no cover - malformed fixture
            raise CatalogFormatError(f"line {index + 1} is not a JSON object: {exc}") from exc
        if not isinstance(data, dict):  # pragma: no cover - malformed fixture
            raise CatalogFormatError(f"line {index + 1} is not a JSON object")
        entry = CatalogEntry(line_index=index, trailing_comma=trailing_comma, data=data)
        if entry.render() != line:
            raise CatalogFormatError(
                f"line {index + 1} does not round-trip; refusing to rewrite the fixture"
            )
        entries.append(entry)
    if not entries:
        raise CatalogFormatError("no catalog entries found")
    return CatalogDocument(lines=lines, entries=entries, trailing_newline=trailing_newline)


def poster_url_from_path(poster_path: object) -> str | None:
    """Build the fixture's poster URL, rejecting anything unusable."""
    if not isinstance(poster_path, str):
        return None
    if not poster_path.startswith("/") or ".." in poster_path:
        return None
    return f"{IMAGE_BASE_URL}/{POSTER_SIZE}{poster_path}"


def split_title_and_year(title: str) -> tuple[str, int | None]:
    """Split ``"Jumanji (1995)"`` into its search terms."""
    if len(title) >= 7 and title.endswith(")") and title[-5:-1].isdigit():
        return title[:-7].strip(), int(title[-5:-1])
    return title.strip(), None


def normalize_title(title: str) -> str:
    return "".join(character for character in title.casefold() if character.isalnum())


def select_targets(entries: Sequence[CatalogEntry], *, refresh: bool) -> list[CatalogEntry]:
    if refresh:
        return list(entries)
    return [entry for entry in entries if entry.poster_url is None]


@dataclass(frozen=True)
class Resolution:
    """What TMDB had for one entry."""

    tmdb_id: str
    poster_url: str | None
    match: str


@dataclass
class Summary:
    filled: list[int] = field(default_factory=list)
    refreshed: list[int] = field(default_factory=list)
    already_had: list[int] = field(default_factory=list)
    no_poster: list[int] = field(default_factory=list)
    unresolved: list[int] = field(default_factory=list)
    fuzzy_matches: list[tuple[int, str, str]] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.filled or self.refreshed)

    def render(self, *, dry_run: bool) -> str:
        verb = "would fill" if dry_run else "filled"
        lines = [
            f"{verb}: {len(self.filled)}",
            f"refreshed: {len(self.refreshed)}",
            f"already had a poster: {len(self.already_had)}",
            f"no poster on TMDB: {len(self.no_poster)}",
            f"unresolved on TMDB: {len(self.unresolved)}",
            f"errors: {len(self.errors)}",
        ]
        if self.no_poster:
            lines.append(f"  no poster: {_format_ids(self.no_poster)}")
        if self.unresolved:
            lines.append(f"  unresolved: {_format_ids(self.unresolved)}")
        if self.fuzzy_matches:
            lines.append("  review (matched by year and search rank, not by exact title):")
            lines.extend(
                f"    {movie_id} {title!r} -> TMDB {tmdb_id}"
                for movie_id, title, tmdb_id in self.fuzzy_matches
            )
        for movie_id, message in self.errors:
            lines.append(f"  error: {movie_id}: {message}")
        return "\n".join(lines)


def _format_ids(movie_ids: Sequence[int]) -> str:
    return ", ".join(str(movie_id) for movie_id in movie_ids)


class TmdbCatalogClient:
    """Minimal, paced TMDB reader for offline fixture enrichment."""

    def __init__(
        self,
        *,
        read_access_token: str,
        client: httpx.Client,
        api_base_url: str = API_BASE_URL,
        min_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._token = read_access_token
        self._client = client
        self._api_base_url = api_base_url.rstrip("/")
        self._min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def movie(self, tmdb_id: str) -> dict[str, Any] | None:
        """Return the movie payload, or ``None`` if TMDB does not have that id."""
        response = self._get(f"/movie/{tmdb_id}", {"language": "en-US"})
        if response.status_code == 404:
            return None
        return _json_object(response)

    def search(self, title: str, year: int | None) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "query": title,
            "language": "en-US",
            "include_adult": "false",
            "page": "1",
        }
        if year is not None:
            params["primary_release_year"] = str(year)
        payload = _json_object(self._get("/search/movie", params))
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        return [result for result in results if isinstance(result, dict)]

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        response = self._request(path, params)
        if response.status_code == 429 or response.status_code >= 500:
            # One retry only. A second failure is a real upstream problem and
            # the run should end non-zero rather than grind through the fixture.
            self._sleep(_retry_wait_seconds(response))
            response = self._request(path, params)
        if response.status_code != 404 and response.status_code >= 400:
            raise TmdbRequestError(f"GET {path} returned HTTP {response.status_code}")
        return response

    def _request(self, path: str, params: dict[str, str]) -> httpx.Response:
        self._pace()
        try:
            return self._client.get(
                f"{self._api_base_url}{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise TmdbRequestError(f"GET {path} failed: {type(exc).__name__}") from exc

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = self._min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at = now


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise TmdbRequestError(f"{response.request.url.path} returned non-JSON") from exc
    if not isinstance(payload, dict):
        raise TmdbRequestError(f"{response.request.url.path} did not return an object")
    return payload


def _retry_wait_seconds(response: httpx.Response) -> float:
    header = response.headers.get("Retry-After", "")
    try:
        wait = float(header)
    except ValueError:
        wait = 1.0
    return min(max(wait, 0.0), MAX_RETRY_WAIT_SECONDS)


def resolve_entry(client: TmdbCatalogClient, entry: CatalogEntry) -> Resolution | None:
    """Look up one entry, resolving its TMDB id by title first if it has none."""
    if entry.tmdb_id is not None:
        payload = client.movie(entry.tmdb_id)
        if payload is None:
            return None
        return Resolution(
            tmdb_id=entry.tmdb_id,
            poster_url=poster_url_from_path(payload.get("poster_path")),
            match="id",
        )

    title, year = split_title_and_year(entry.title)
    results = client.search(title, year)
    exact = _first_exact_match(results, title)
    if exact is not None:
        return _resolution_from_result(exact, match="search-exact")
    if results and year is not None:
        # The search was already constrained to the release year the fixture
        # claims, so the top-ranked survivor is the candidate a human would
        # check first. It is reported for review rather than trusted silently.
        return _resolution_from_result(results[0], match="search-ranked")
    if year is not None:
        # A MovieLens year and a TMDB primary release year can disagree by one.
        # Widening the search is only safe with an exact title, so require it.
        widened = _first_exact_match(client.search(title, None), title)
        if widened is not None:
            return _resolution_from_result(widened, match="search-exact")
    return None


def _first_exact_match(results: Sequence[dict[str, Any]], title: str) -> dict[str, Any] | None:
    wanted = normalize_title(title)
    for result in results:
        candidates = (result.get("title"), result.get("original_title"))
        if any(isinstance(name, str) and normalize_title(name) == wanted for name in candidates):
            return result
    return None


def _resolution_from_result(result: dict[str, Any], *, match: str) -> Resolution | None:
    tmdb_id = result.get("id")
    if not isinstance(tmdb_id, int) or tmdb_id <= 0:
        return None
    return Resolution(
        tmdb_id=str(tmdb_id),
        poster_url=poster_url_from_path(result.get("poster_path")),
        match=match,
    )


def enrich(
    document: CatalogDocument,
    client: TmdbCatalogClient,
    *,
    refresh: bool,
) -> Summary:
    """Fill poster URLs in place and report what happened to every entry."""
    summary = Summary()
    targets = select_targets(document.entries, refresh=refresh)
    target_ids = {id(entry) for entry in targets}
    summary.already_had.extend(
        entry.movie_id
        for entry in document.entries
        if id(entry) not in target_ids and entry.poster_url is not None
    )

    for entry in targets:
        try:
            resolution = resolve_entry(client, entry)
        except TmdbRequestError as exc:
            summary.errors.append((entry.movie_id, str(exc)))
            continue
        if resolution is None:
            summary.unresolved.append(entry.movie_id)
            continue
        if resolution.match == "search-ranked":
            summary.fuzzy_matches.append((entry.movie_id, entry.title, resolution.tmdb_id))
        if resolution.poster_url is None:
            summary.no_poster.append(entry.movie_id)
            # Still worth keeping the id we resolved: the next run becomes a
            # plain id lookup, which is cheaper and no longer needs a match.
            if entry.tmdb_id is None:
                entry.set_fields(tmdb_id=resolution.tmdb_id)
                summary.refreshed.append(entry.movie_id)
            continue

        had_poster = entry.poster_url is not None
        if entry.poster_url == resolution.poster_url and entry.tmdb_id == resolution.tmdb_id:
            summary.already_had.append(entry.movie_id)
            continue
        entry.set_fields(tmdb_id=resolution.tmdb_id, poster_url=resolution.poster_url)
        (summary.refreshed if had_poster else summary.filled).append(entry.movie_id)

    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m synthetic.personas.enrich_posters",
        description="Fill poster URLs in the reviewed demo catalog fixture from TMDB.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be fetched and never write the fixture",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--only-missing",
        action="store_true",
        help="fill entries that have no poster_url (the default)",
    )
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch every entry and overwrite existing poster URLs",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=CATALOG_PATH,
        help=f"catalog fixture to enrich (default: {CATALOG_PATH})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        print("TMDB_READ_ACCESS_TOKEN is not set; export it and re-run.", file=sys.stderr)
        return 2

    catalog_path = Path(args.catalog)
    document = parse_catalog(catalog_path.read_text(encoding="utf-8"))
    targets = select_targets(document.entries, refresh=args.refresh)

    if args.dry_run:
        # A dry run makes no requests at all, so it stays usable as a cheap
        # "is there anything left to do?" check against the committed fixture.
        print(f"{len(targets)} of {len(document.entries)} entries would be fetched from TMDB.")
        if targets:
            print(f"  {_format_ids([entry.movie_id for entry in targets])}")
        return 0

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        client = TmdbCatalogClient(read_access_token=token, client=http_client)
        summary = enrich(document, client, refresh=args.refresh)

    if summary.changed:
        catalog_path.write_text(document.render(), encoding="utf-8")
    print(summary.render(dry_run=False))
    print(f"{'wrote' if summary.changed else 'left unchanged'} {catalog_path}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
