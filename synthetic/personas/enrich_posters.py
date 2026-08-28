"""Offline poster and synopsis enrichment for the reviewed demo catalog fixture.

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
    python -m synthetic.personas.enrich_posters --refresh --only 17,21,1247
    python -m synthetic.personas.enrich_posters --verify   # no token needed

The token is read from ``TMDB_READ_ACCESS_TOKEN`` and from nowhere else — it is
never written into the repository, and the FastAPI service does not need it for
the catalog path. Default behaviour fills the entries that are missing a
``poster_url`` or an ``overview``; ``--refresh`` re-fetches every entry and
overwrites the poster URL; ``--only`` narrows either mode to named movie ids so
repairing three stale paths does not churn the other hundred-odd. Every mode is
idempotent: a second run over an already-enriched fixture changes no bytes.

An overview is only ever *filled*, never replaced. The reviewed sentences in the
fixture are hand-written and shorter than TMDB's own copy; an automated pass has
no business overwriting a human's editorial choice.

Nothing is written before it is checked. A poster URL is HEADed against the
image CDN before it lands in the fixture, and ``--verify`` re-HEADs every stored
URL — that is what stops the fixture rotting silently when TMDB re-cuts artwork
and leaves the old path 404ing. ``--verify`` needs no credentials (the image CDN
is public) and makes real network calls, so it is a ``make catalog-verify``
target and a nightly habit, never a PR gate: a third party's uptime must not
decide whether a pull request is mergeable. The offline half of the check — that
every entry carries a URL in the pinned ``…/t/p/w500/`` shape — is a plain unit
test and does gate CI.

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
import re
import sys
import time
from collections.abc import Callable, Collection, Sequence
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
# The one poster URL shape the fixture is allowed to carry. It is pinned in
# three other places — ``web/next.config.ts``'s remote-image allow-list,
# ``quick-picks-deck.tsx`` and ``src/serving/tmdb.py`` — so a URL that does not
# match here is a URL the browser would refuse to load even if it resolved.
POSTER_URL_PREFIX = f"{IMAGE_BASE_URL}/{POSTER_SIZE}/"
_POSTER_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]+$")

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

    @property
    def overview(self) -> str | None:
        value = self.data.get("overview")
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


def poster_url_shape_error(url: object) -> str | None:
    """Say why a stored poster URL is not the pinned shape, or ``None`` if it is.

    Offline on purpose. This is the half of the liveness gate that can run in
    CI: it catches a hand-edited host, a different poster size, or a query
    string smuggled onto the path without asking a third party anything.
    """
    if not isinstance(url, str) or not url:
        return "no poster_url"
    if not url.startswith(POSTER_URL_PREFIX):
        return f"not the pinned {POSTER_URL_PREFIX}<file> shape"
    filename = url[len(POSTER_URL_PREFIX) :]
    if not _POSTER_FILENAME.fullmatch(filename):
        return f"poster path {filename!r} is not a plain image file name"
    return None


def split_title_and_year(title: str) -> tuple[str, int | None]:
    """Split ``"Jumanji (1995)"`` into its search terms."""
    if len(title) >= 7 and title.endswith(")") and title[-5:-1].isdigit():
        return title[:-7].strip(), int(title[-5:-1])
    return title.strip(), None


def normalize_title(title: str) -> str:
    return "".join(character for character in title.casefold() if character.isalnum())


def select_targets(
    entries: Sequence[CatalogEntry],
    *,
    refresh: bool,
    only: Collection[int] | None = None,
) -> list[CatalogEntry]:
    """Pick the entries a run will fetch.

    The default is "anything this script could still fill" — a missing poster
    *or* a missing overview — because the fixture reached 120/120 posters while
    96 titles were still synopsis-less, and a mode that only looked at posters
    had nothing left to do. ``only`` narrows whatever the mode selected to a
    named set of ids, which is how three stale poster paths get repaired
    without re-fetching (and possibly re-writing) the other 117.
    """
    selected = [
        entry for entry in entries if refresh or entry.poster_url is None or entry.overview is None
    ]
    if only is None:
        return selected
    return [entry for entry in selected if entry.movie_id in only]


@dataclass(frozen=True)
class Resolution:
    """What TMDB had for one entry."""

    tmdb_id: str
    poster_url: str | None
    match: str
    overview: str | None = None


@dataclass
class Summary:
    filled: list[int] = field(default_factory=list)
    refreshed: list[int] = field(default_factory=list)
    overviews_filled: list[int] = field(default_factory=list)
    ids_learned: list[int] = field(default_factory=list)
    already_had: list[int] = field(default_factory=list)
    no_poster: list[int] = field(default_factory=list)
    no_overview: list[int] = field(default_factory=list)
    dead_posters: list[tuple[int, str]] = field(default_factory=list)
    unresolved: list[int] = field(default_factory=list)
    fuzzy_matches: list[tuple[int, str, str]] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.filled or self.refreshed or self.overviews_filled or self.ids_learned)

    def render(self, *, dry_run: bool) -> str:
        verb = "would fill" if dry_run else "filled"
        lines = [
            f"{verb}: {len(self.filled)}",
            f"refreshed: {len(self.refreshed)}",
            f"overviews filled: {len(self.overviews_filled)}",
            f"already had a poster: {len(self.already_had)}",
            f"no poster on TMDB: {len(self.no_poster)}",
            f"no overview on TMDB: {len(self.no_overview)}",
            f"dead upstream, not written: {len(self.dead_posters)}",
            f"unresolved on TMDB: {len(self.unresolved)}",
            f"errors: {len(self.errors)}",
        ]
        if self.no_poster:
            lines.append(f"  no poster: {_format_ids(self.no_poster)}")
        if self.no_overview:
            lines.append(f"  no overview: {_format_ids(self.no_overview)}")
        for movie_id, reason in self.dead_posters:
            lines.append(f"  dead upstream: {movie_id}: {reason}")
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


class _Pacer:
    """Keeps a caller from hammering a host harder than a human would."""

    def __init__(
        self,
        *,
        min_interval_seconds: float,
        sleep: Callable[[float], None],
        monotonic: Callable[[], float],
    ) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def wait(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = self._min_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at = now


class PosterVerifier:
    """Answers one question: does this poster URL still resolve?

    Deliberately credential-free. The image CDN is a different host from the
    TMDB API and has no business seeing a read token, and keeping it separate is
    what lets ``--verify`` run with no secret at all — the difference between a
    check anyone can run and one only the fixture's owner can.
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        min_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._pacer = _Pacer(
            min_interval_seconds=min_interval_seconds,
            sleep=sleep,
            monotonic=monotonic,
        )
        self._sleep = sleep

    def check(self, url: str) -> str | None:
        """Return why the URL is unusable, or ``None`` when it resolves."""
        shape = poster_url_shape_error(url)
        if shape is not None:
            return shape
        response = self._head(url)
        if response is None:
            status: str | None = None
        else:
            if response.status_code == 200:
                return None
            status = f"HTTP {response.status_code}"
            # A CDN hiccup should not condemn a poster that is really there, so
            # a throttle or a server error earns the same single retry the API
            # calls get. A 404 is upstream's answer, not a hiccup.
            if response.status_code != 429 and response.status_code < 500:
                return status
            self._sleep(_retry_wait_seconds(response))
        retry = self._head(url)
        if retry is None:
            return status or "request failed"
        if retry.status_code == 200:
            return None
        return f"HTTP {retry.status_code}"

    def _head(self, url: str) -> httpx.Response | None:
        self._pacer.wait()
        try:
            return self._client.head(
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return None


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
        self._sleep = sleep
        self._pacer = _Pacer(
            min_interval_seconds=min_interval_seconds,
            sleep=sleep,
            monotonic=monotonic,
        )
        self._posters = PosterVerifier(
            client=client,
            min_interval_seconds=min_interval_seconds,
            sleep=sleep,
            monotonic=monotonic,
        )

    def poster_check(self, url: str) -> str | None:
        """Return why a poster URL is unusable, or ``None`` when it resolves."""
        return self._posters.check(url)

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
        self._pacer.wait()
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
            overview=clean_overview(payload.get("overview")),
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
        overview=clean_overview(result.get("overview")),
    )


def clean_overview(value: object) -> str | None:
    """Normalize TMDB's synopsis, treating its empty string as "none"."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def enrich(
    document: CatalogDocument,
    client: TmdbCatalogClient,
    *,
    refresh: bool,
    only: Collection[int] | None = None,
) -> Summary:
    """Fill poster URLs and synopses in place, reporting every entry's outcome."""
    summary = Summary()
    targets = select_targets(document.entries, refresh=refresh, only=only)
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

        fields: dict[str, str] = {}
        # Worth keeping even when nothing else lands: the next run becomes a
        # plain id lookup, which is cheaper and no longer needs a match.
        if entry.tmdb_id != resolution.tmdb_id:
            fields["tmdb_id"] = resolution.tmdb_id
        _plan_poster(client, entry, resolution, fields, summary, refresh=refresh)
        _plan_overview(entry, resolution, fields, summary)

        if not fields:
            continue
        entry.set_fields(**fields)
        if "poster_url" not in fields and "overview" not in fields:
            summary.ids_learned.append(entry.movie_id)

    return summary


def _plan_poster(
    client: TmdbCatalogClient,
    entry: CatalogEntry,
    resolution: Resolution,
    fields: dict[str, str],
    summary: Summary,
    *,
    refresh: bool,
) -> None:
    """Decide what happens to one entry's poster URL, and prove it resolves.

    An entry keeps the poster it already has unless ``--refresh`` asks for a
    replacement: a run whose real job is filling synopses must not quietly churn
    a hundred artwork URLs because TMDB re-cropped them this month.
    """
    if resolution.poster_url is None:
        summary.no_poster.append(entry.movie_id)
        return
    if entry.poster_url is not None and (not refresh or entry.poster_url == resolution.poster_url):
        summary.already_had.append(entry.movie_id)
        return
    reason = client.poster_check(resolution.poster_url)
    if reason is not None:
        # Writing a URL that is already known to 404 is exactly how the fixture
        # rotted the first time. Report it and leave the slot as it was.
        summary.dead_posters.append((entry.movie_id, reason))
        return
    fields["poster_url"] = resolution.poster_url
    (summary.refreshed if entry.poster_url is not None else summary.filled).append(entry.movie_id)


def _plan_overview(
    entry: CatalogEntry,
    resolution: Resolution,
    fields: dict[str, str],
    summary: Summary,
) -> None:
    """Fill a missing synopsis; never replace one a human wrote.

    ``--refresh`` deliberately does not reach the overview. The reviewed
    sentences in the fixture are shorter and more deliberate than TMDB's own
    copy, and an automated pass overwriting editorial text is a diff nobody
    asked for.
    """
    if entry.overview is not None:
        return
    if resolution.overview is None:
        summary.no_overview.append(entry.movie_id)
        return
    fields["overview"] = resolution.overview
    summary.overviews_filled.append(entry.movie_id)


@dataclass(frozen=True)
class VerifyFailure:
    movie_id: int
    title: str
    url: str | None
    reason: str


@dataclass
class VerifyReport:
    """What ``--verify`` found, in the order a reader wants it."""

    checked: int
    failures: list[VerifyFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def render(self) -> str:
        lines = [
            f"checked: {self.checked}",
            f"failures: {len(self.failures)}",
        ]
        lines.extend(
            f"  {failure.movie_id} {failure.title!r}: {failure.reason}"
            f"{'' if failure.url is None else f' ({failure.url})'}"
            for failure in self.failures
        )
        return "\n".join(lines)


def verify_catalog(
    document: CatalogDocument,
    verifier: PosterVerifier,
    *,
    only: Collection[int] | None = None,
) -> VerifyReport:
    """HEAD every stored poster URL and report the ones that no longer resolve."""
    entries = [entry for entry in document.entries if only is None or entry.movie_id in only]
    report = VerifyReport(checked=len(entries))
    for entry in entries:
        url = entry.poster_url
        if url is None:
            # Every visible title is supposed to carry artwork: the fixture is
            # the only poster source on the request path, so a gap here is a
            # permanent placeholder rather than a state a viewer passes through.
            report.failures.append(
                VerifyFailure(entry.movie_id, entry.title, None, "no poster_url")
            )
            continue
        reason = verifier.check(url)
        if reason is not None:
            report.failures.append(VerifyFailure(entry.movie_id, entry.title, url, reason))
    return report


def _parse_only(value: str | None) -> set[int] | None:
    if value is None:
        return None
    ids = {int(part) for part in value.replace(",", " ").split()}
    if not ids:
        raise argparse.ArgumentTypeError("--only needs at least one movie id")
    return ids


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m synthetic.personas.enrich_posters",
        description=(
            "Fill poster URLs and synopses in the reviewed demo catalog fixture "
            "from TMDB, and verify that the stored posters still resolve."
        ),
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
        help="fill entries missing a poster_url or an overview (the default)",
    )
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch every entry and overwrite existing poster URLs",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help=(
            "HEAD every stored poster_url and exit non-zero on any that is "
            "missing, misshapen or dead upstream; needs no TMDB token"
        ),
    )
    parser.add_argument(
        "--only",
        type=_parse_only,
        default=None,
        metavar="ID[,ID...]",
        help="restrict the run to these movie ids",
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

    catalog_path = Path(args.catalog)
    document = parse_catalog(catalog_path.read_text(encoding="utf-8"))

    if args.verify:
        # Verification reads a public CDN and writes nothing, so it deliberately
        # runs without the token: anyone can check the fixture has not rotted,
        # only its owner can refill it.
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
            report = verify_catalog(
                document,
                PosterVerifier(client=http_client),
                only=args.only,
            )
        print(report.render())
        return 0 if report.ok else 1

    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        print("TMDB_READ_ACCESS_TOKEN is not set; export it and re-run.", file=sys.stderr)
        return 2

    targets = select_targets(document.entries, refresh=args.refresh, only=args.only)

    if args.dry_run:
        # A dry run makes no requests at all, so it stays usable as a cheap
        # "is there anything left to do?" check against the committed fixture.
        print(f"{len(targets)} of {len(document.entries)} entries would be fetched from TMDB.")
        if targets:
            print(f"  {_format_ids([entry.movie_id for entry in targets])}")
        return 0

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        client = TmdbCatalogClient(read_access_token=token, client=http_client)
        summary = enrich(document, client, refresh=args.refresh, only=args.only)

    if summary.changed:
        catalog_path.write_text(document.render(), encoding="utf-8")
    print(summary.render(dry_run=False))
    print(f"{'wrote' if summary.changed else 'left unchanged'} {catalog_path}")
    # A poster TMDB offered but the image host would not serve is a failure, not
    # a shrug: it is the exact condition that left three fixture titles dead.
    return 1 if summary.errors or summary.dead_posters else 0


if __name__ == "__main__":
    raise SystemExit(main())
