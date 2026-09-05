"""Snapshot every TMDB detail payload the MovieLens catalog can reach.

MovieLens ships `links.csv`, which maps 62,316 of the 62,423 catalog movies to a
TMDB id. This module walks that mapping once, asks TMDB for the full detail
payload of each film, and writes what comes back to gzipped JSONL shards under
``data/raw/tmdb/<pull-date>/``. The shards are DVC-tracked and never committed
to git, exactly like the MovieLens CSVs — the snapshot is data, not source.

Why a snapshot rather than a live call. ADR 0017's third risk says it plainly:
"TMDB is an external dependency with rate limits, terms of use, and data that
changes underneath a cached copy. Any ingestion must be snapshot-versioned like
the ratings frame, or it silently breaks the reproducibility guarantee in
non-negotiable #5." A model trained against a live API is not reproducible, so
the API is called once and the answer is versioned.

**One request per movie.** ``append_to_response`` folds keywords, credits,
release dates, external ids, alternative titles and translations into the same
response TMDB was already going to send, so the whole catalog costs 62,316
requests rather than seven times that.

**``images`` is deliberately not appended.** It returns every poster, backdrop
and logo TMDB holds for the film in every language it holds them — commonly a
hundred to three hundred entries — and the only two artwork paths this system
has ever used, ``poster_path`` and ``backdrop_path``, are already in the base
payload. Appending it would roughly double the size of the snapshot in exchange
for nothing a recommender or the product can use.

**Rate limiting.** TMDB's published guidance sits around 40–50 requests a
second. This runs at 20 by default and never raises it: a snapshot that takes
52 minutes instead of 21 is a trade worth making against any chance of the key
being blocked, and the whole run happens once. On a 429 the client honours
``Retry-After``, backs off exponentially with jitter, and — if throttling keeps
happening — halves its own rate rather than continuing to lean on the limit.

**Resumability.** A record is written for every id that resolved *and* for every
id TMDB answered 404 for, so a re-run reads the shards already on disk, skips
what they contain, and asks only for what is missing. That makes the pull
idempotent: running it twice produces the same snapshot and the second run costs
almost no requests. Ids that *failed* are deliberately not recorded as skippable
— a transient failure should be retried by the next run, not baked in.

The read token comes from ``TMDB_READ_ACCESS_TOKEN`` and from nowhere else. It
is never logged, never written into a shard or the manifest, and never committed.

    set -a && . /path/to/tmdb.env && set +a     # TMDB_READ_ACCESS_TOKEN
    make tmdb-ingest ARGS="--limit 200"         # smoke
    make tmdb-ingest                            # the full catalog

Attribution: this product uses the TMDB API but is not endorsed or certified by
TMDB. The web app already renders that notice for the poster path; this pass
adds no new obligation beyond keeping it visible.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import logging
import os
import random
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)

TMDB_API_VERSION = "3"
TOKEN_ENV_VAR = "TMDB_READ_ACCESS_TOKEN"

# Six appends, one request. `images` is excluded on purpose — see the module
# docstring. TMDB caps `append_to_response` at 20 sub-requests; we use six.
APPEND_TO_RESPONSE = "keywords,credits,release_dates,external_ids,alternative_titles,translations"
LANGUAGE = "en-US"

DEFAULT_REQUESTS_PER_SECOND = 20.0
# The floor the automatic halving will not go below. Below this the run stops
# being a snapshot and starts being a week-long job; if TMDB is throttling this
# hard, the right move is to stop and look rather than to crawl.
MIN_REQUESTS_PER_SECOND = 2.5
# How many throttled responses at the current rate before the client halves it.
DEFAULT_THROTTLE_TOLERANCE = 5
DEFAULT_SHARD_SIZE = 2_000
DEFAULT_MAX_ATTEMPTS = 4
# A hard stop is a circuit breaker, not a retry budget. Ten failures in a row
# means TMDB is down, the network is gone, or the key has been throttled into
# uselessness — none of which get better by sending request eleven.
DEFAULT_MAX_CONSECUTIVE_FAILURES = 10
DEFAULT_PROGRESS_EVERY = 1_000

REQUEST_TIMEOUT_SECONDS = 20.0
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0

SHARD_PREFIX = "movies"
SHARD_SUFFIX = ".jsonl.gz"
PARTIAL_SUFFIX = ".partial"
MANIFEST_NAME = "manifest.json"


class TmdbIngestError(RuntimeError):
    """A TMDB request failed in a way the pull cannot route around."""


class TmdbAccessDeniedError(TmdbIngestError):
    """TMDB rejected the credentials — 401 or 403.

    Its own class because it is the one failure that must never be retried: a
    rejected key retried in a loop is how a key gets blocked outright, which is
    the single outcome this module is written to avoid.
    """


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


class TokenBucket:
    """A refilling bucket that blocks until the next request is allowed.

    A bucket rather than a fixed sleep between calls because the two behave
    differently after a stall: a fixed pacer that has been waiting on a slow
    response spends the wait doing nothing and then still pauses, while a bucket
    accrues credit and lets the run catch back up to its configured average
    without ever exceeding it over any window longer than the burst.

    ``monotonic`` and ``sleep`` are injected so the tests can drive it on a fake
    clock — a rate limiter tested by actually waiting is a rate limiter nobody
    runs in CI.
    """

    def __init__(
        self,
        *,
        rate_per_second: float,
        capacity: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._rate = rate_per_second
        self._capacity = capacity if capacity is not None else max(1.0, rate_per_second)
        self._tokens = self._capacity
        self._monotonic = monotonic
        self._sleep = sleep
        self._updated = monotonic()

    @property
    def rate_per_second(self) -> float:
        return self._rate

    def set_rate(self, rate_per_second: float) -> None:
        """Re-rate the bucket in place, keeping the credit already accrued."""
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._refill()
        self._rate = rate_per_second
        self._capacity = max(1.0, rate_per_second)
        self._tokens = min(self._tokens, self._capacity)

    def take(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available; return the seconds slept."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return 0.0
        wait = (tokens - self._tokens) / self._rate
        self._sleep(wait)
        self._refill()
        self._tokens = max(0.0, self._tokens - tokens)
        return wait

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    """What one movie's fetch produced.

    ``raw`` is the response body exactly as TMDB sent it, kept as text rather
    than as a parsed object so the shard can embed the original bytes instead of
    a re-serialization of them.
    """

    tmdb_id: int
    status: str  # "ok" | "not_found"
    raw: str | None
    attempts: int


class TmdbDetailClient:
    """Paced, backing-off reader for ``/movie/{id}`` with the appends folded in.

    The retry policy is deliberately narrow. 429 and 5xx are retried, because
    they are the two answers that mean "ask again later". 404 is not an error at
    all — MovieLens carries TMDB ids that TMDB has since merged or removed, and
    a 404 is a fact about the catalog worth recording. Every other 4xx is a bug
    in the request and retrying it just burns quota. 401 and 403 stop the run.
    """

    def __init__(
        self,
        *,
        read_access_token: str,
        client: httpx.Client,
        bucket: TokenBucket,
        api_base_url: str = "https://api.themoviedb.org/3",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        throttle_tolerance: int = DEFAULT_THROTTLE_TOLERANCE,
        min_rate_per_second: float = MIN_REQUESTS_PER_SECOND,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._token = read_access_token
        self._client = client
        self._bucket = bucket
        self._api_base_url = api_base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._throttle_tolerance = max(1, throttle_tolerance)
        self._min_rate = min_rate_per_second
        self._sleep = sleep
        self._rng = rng

        self.requests_sent = 0
        self.throttled_responses = 0
        self.server_error_responses = 0
        self.rate_reductions = 0
        self._throttles_at_current_rate = 0

    @property
    def rate_per_second(self) -> float:
        """The rate the bucket is on now, which is not the configured one after
        a halving. The manifest records both."""
        return self._bucket.rate_per_second

    def fetch(self, tmdb_id: int) -> FetchResult:
        """Fetch one movie, retrying only what is worth retrying.

        Raises ``TmdbIngestError`` when the attempt budget is exhausted, so the
        caller owns the decision about whether that is one bad id or a dead
        upstream.
        """
        last_problem = "unknown"
        for attempt in range(self._max_attempts):
            response = self._send(tmdb_id)
            if response is None:
                last_problem = "transport error"
                self._back_off(attempt, None)
                continue

            status = response.status_code
            if status == 200:
                return FetchResult(
                    tmdb_id=tmdb_id, status="ok", raw=response.text, attempts=attempt + 1
                )
            if status == 404:
                return FetchResult(
                    tmdb_id=tmdb_id, status="not_found", raw=None, attempts=attempt + 1
                )
            if status in (401, 403):
                raise TmdbAccessDeniedError(
                    f"TMDB rejected the read token with HTTP {status}. "
                    "Stopping rather than retrying — a rejected key retried in a "
                    "loop is how a key gets blocked."
                )
            if status == 429:
                self.throttled_responses += 1
                self._maybe_slow_down()
                last_problem = "HTTP 429"
                self._back_off(attempt, response)
                continue
            if status >= 500:
                self.server_error_responses += 1
                last_problem = f"HTTP {status}"
                self._back_off(attempt, response)
                continue
            raise TmdbIngestError(f"GET /movie/{tmdb_id} returned HTTP {status}")

        raise TmdbIngestError(
            f"GET /movie/{tmdb_id} gave up after {self._max_attempts} attempts ({last_problem})"
        )

    def _send(self, tmdb_id: int) -> httpx.Response | None:
        self._bucket.take()
        self.requests_sent += 1
        try:
            return self._client.get(
                f"{self._api_base_url}/movie/{tmdb_id}",
                params={"language": LANGUAGE, "append_to_response": APPEND_TO_RESPONSE},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            # A transport failure is indistinguishable from a 5xx as far as the
            # retry policy is concerned, and the exception type carries nothing
            # the caller can act on.
            return None

    def _back_off(self, attempt: int, response: httpx.Response | None) -> None:
        self._sleep(self._backoff_seconds(attempt, response))

    def _backoff_seconds(self, attempt: int, response: httpx.Response | None) -> float:
        """``Retry-After`` when TMDB gave one, exponential with jitter otherwise.

        Jitter is on both paths. Without it a batch of ids that hit the same 429
        would come back in lockstep and throttle each other again, which is the
        thundering-herd shape the retry is supposed to defuse.
        """
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            return min(MAX_BACKOFF_SECONDS, retry_after + self._rng())
        ceiling = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2.0**attempt))
        return ceiling * (0.5 + 0.5 * self._rng())

    def _maybe_slow_down(self) -> None:
        """Halve the configured rate once throttling stops looking like a blip.

        The bucket is already under TMDB's published guidance, so a 429 means
        our reading of that guidance is wrong for this key on this day. Halving
        is the response the owner asked for, automated so it does not depend on
        somebody watching the log.
        """
        self._throttles_at_current_rate += 1
        if self._throttles_at_current_rate < self._throttle_tolerance:
            return
        self._throttles_at_current_rate = 0
        current = self._bucket.rate_per_second
        if current <= self._min_rate:
            return
        reduced = max(self._min_rate, current / 2.0)
        self._bucket.set_rate(reduced)
        self.rate_reductions += 1
        logger.warning(
            "TMDB throttled %d responses at %.1f req/s — halving to %.1f req/s",
            self.throttled_responses,
            current,
            reduced,
        )


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    """Parse ``Retry-After`` in either shape the RFC allows."""
    if response is None:
        return None
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


# ---------------------------------------------------------------------------
# Shards
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShardInfo:
    name: str
    records: int
    sha256: str
    bytes: int


class ShardWriter:
    """Writes newline-delimited JSON into rotating gzip shards.

    A shard is written under a ``.partial`` name and renamed when it is full or
    when the writer is closed, so a finished ``.jsonl.gz`` is always a complete,
    readable file. A run that is killed outright leaves at most one ``.partial``
    behind; the next run deletes it and re-fetches the few hundred ids it held,
    which is cheaper than the alternative of trying to salvage a truncated gzip
    stream and getting it subtly wrong.
    """

    def __init__(self, out_dir: Path, *, shard_size: int = DEFAULT_SHARD_SIZE) -> None:
        if shard_size <= 0:
            raise ValueError("shard_size must be positive")
        self._dir = out_dir
        self._shard_size = shard_size
        self._gzip: gzip.GzipFile | None = None
        self._raw: BinaryIO | None = None
        self._partial_path: Path | None = None
        self._records_in_shard = 0
        self._index = _next_shard_index(out_dir)
        self.records_written = 0
        self.shards: list[ShardInfo] = []

    def write(self, line: str) -> None:
        handle = self._gzip if self._gzip is not None else self._open()
        handle.write(f"{line}\n".encode())
        self._records_in_shard += 1
        self.records_written += 1
        if self._records_in_shard >= self._shard_size:
            self._finish()

    def close(self) -> None:
        self._finish()

    def _open(self) -> gzip.GzipFile:
        self._dir.mkdir(parents=True, exist_ok=True)
        name = f"{SHARD_PREFIX}-{self._index:05d}{SHARD_SUFFIX}"
        self._partial_path = self._dir / f"{name}{PARTIAL_SUFFIX}"
        self._raw = self._partial_path.open("wb")
        # mtime=0 and an explicit filename so a shard's bytes depend only on its
        # contents. Two runs that fetched the same ids in the same order then
        # produce the same file, and `dvc status` stays quiet unless the data
        # actually moved — which is the whole point of versioning the snapshot.
        self._gzip = gzip.GzipFile(filename=name, mode="wb", fileobj=self._raw, mtime=0)
        self._records_in_shard = 0
        return self._gzip

    def _finish(self) -> None:
        if self._gzip is None or self._partial_path is None:
            return
        self._gzip.close()
        if self._raw is not None:
            self._raw.close()
        final = self._partial_path.with_suffix("")  # strips ".partial"
        self._partial_path.rename(final)
        self.shards.append(_describe_shard(final, self._records_in_shard))
        self._gzip = None
        self._raw = None
        self._partial_path = None
        self._records_in_shard = 0
        self._index += 1


def _describe_shard(path: Path, records: int) -> ShardInfo:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return ShardInfo(
        name=path.name, records=records, sha256=digest.hexdigest(), bytes=path.stat().st_size
    )


def _next_shard_index(out_dir: Path) -> int:
    highest = -1
    for path in shard_paths(out_dir):
        stem = path.name[len(SHARD_PREFIX) + 1 : -len(SHARD_SUFFIX)]
        try:
            highest = max(highest, int(stem))
        except ValueError:
            continue
    return highest + 1


def shard_paths(out_dir: Path) -> list[Path]:
    """Every complete shard in ``out_dir``, in name order."""
    if not out_dir.is_dir():
        return []
    return sorted(out_dir.glob(f"{SHARD_PREFIX}-*{SHARD_SUFFIX}"))


def read_shard_records(out_dir: Path) -> Iterator[dict[str, Any]]:
    """Yield every record in every shard, oldest shard first."""
    for path in shard_paths(out_dir):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if isinstance(record, dict):
                    yield record


def completed_tmdb_ids(out_dir: Path) -> set[int]:
    """The ids already snapshotted — resolved *or* answered 404.

    Failures are absent by construction, so a resume retries them. That is the
    property that makes "run it again" the fix for a flaky network.
    """
    done: set[int] = set()
    for record in read_shard_records(out_dir):
        tmdb_id = record.get("tmdb_id")
        if isinstance(tmdb_id, int):
            done.add(tmdb_id)
    return done


def discard_partial_shards(out_dir: Path) -> int:
    """Delete any shard a killed run left half-written. Returns how many."""
    if not out_dir.is_dir():
        return 0
    partials = sorted(out_dir.glob(f"*{PARTIAL_SUFFIX}"))
    for path in partials:
        path.unlink()
    return len(partials)


def build_record(*, movie_ids: Sequence[int], result: FetchResult, fetched_at: str) -> str:
    """One JSONL line: a small provenance envelope around TMDB's own bytes.

    The payload is embedded verbatim — the exact body TMDB returned, not a
    re-serialization of a parsed copy — so nothing this code believes about the
    shape of a TMDB response can quietly edit the snapshot. The one case where
    the original text is not usable as-is is a pretty-printed body, which would
    break the one-object-per-line contract; that is re-encoded compactly, and it
    is the only transformation this function will ever apply.

    ``movie_ids`` is a list rather than a scalar because the mapping is not
    one-to-one: 34 TMDB ids in ``links.csv`` are claimed by two MovieLens movies
    each (69 rows in all), which are duplicate catalog entries for the same
    film. One request answers for both, and the loader fans the payload back out.
    """
    envelope = {
        "movie_ids": list(movie_ids),
        "tmdb_id": result.tmdb_id,
        "status": result.status,
        "fetched_at": fetched_at,
    }
    head = json.dumps(envelope, ensure_ascii=False)[:-1]  # drop the closing brace
    if result.raw is None:
        return head + ',"payload":null}'
    body = result.raw
    if "\n" in body or "\r" in body:
        body = json.dumps(json.loads(body), ensure_ascii=False, separators=(",", ":"))
    return head + ',"payload":' + body + "}"


# ---------------------------------------------------------------------------
# The pull
# ---------------------------------------------------------------------------


@dataclass
class PullStats:
    requested: int = 0
    ok: int = 0
    not_found: int = 0
    failed: int = 0
    skipped: int = 0
    failed_ids: list[int] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str | None = None


@dataclass(frozen=True)
class CatalogLink:
    """One TMDB id and every MovieLens movie that claims it."""

    tmdb_id: int
    movie_ids: tuple[int, ...]


def read_links(links_csv: Path) -> list[CatalogLink]:
    """Parse ``links.csv`` into one entry per distinct TMDB id.

    Grouped by ``tmdbId`` rather than listed per movie because the mapping is
    not injective — 34 TMDB ids are claimed by two MovieLens movies each, which
    are duplicate catalog rows for the same film. Iterating rows instead of ids
    would send 34 needless requests and, worse, the resume logic would skip the
    second row of each pair on a restart and drop it from the snapshot.

    Rows with a blank ``tmdbId`` are dropped here rather than fetched and
    recorded as failures — MovieLens genuinely does not know a TMDB id for them,
    which is a gap in the mapping, not a gap in the snapshot. The coverage report
    counts them separately.
    """
    grouped: dict[int, list[int]] = {}
    with links_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_tmdb = (row.get("tmdbId") or "").strip()
            if not raw_tmdb:
                continue
            try:
                movie_id = int(row["movieId"])
                tmdb_id = int(float(raw_tmdb))
            except (KeyError, TypeError, ValueError):
                continue
            grouped.setdefault(tmdb_id, []).append(movie_id)
    # Ordered by the lowest MovieLens id that claims each TMDB id, so the shards
    # come out in a stable order and a re-pull writes the same bytes.
    return sorted(
        (
            CatalogLink(tmdb_id=tmdb_id, movie_ids=tuple(sorted(ids)))
            for tmdb_id, ids in grouped.items()
        ),
        key=lambda link: link.movie_ids[0],
    )


def run_pull(
    *,
    links: Sequence[CatalogLink],
    client: TmdbDetailClient,
    out_dir: Path,
    shard_size: int = DEFAULT_SHARD_SIZE,
    limit: int | None = None,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[PullStats, list[ShardInfo]]:
    """Fetch every link not already on disk and append it to the shards."""
    discarded = discard_partial_shards(out_dir)
    if discarded:
        logger.info("Dropped %d partial shard(s) from an interrupted run.", discarded)

    already = completed_tmdb_ids(out_dir)
    stats = PullStats(skipped=0)
    pending = [link for link in links if link.tmdb_id not in already]
    stats.skipped = len(links) - len(pending)
    if limit is not None:
        pending = pending[:limit]
    stats.requested = len(pending)

    if stats.skipped:
        logger.info("Resuming: %d ids already on disk, %d to fetch.", stats.skipped, len(pending))

    writer = ShardWriter(out_dir, shard_size=shard_size)
    consecutive_failures = 0
    started = time.monotonic()
    try:
        for index, link in enumerate(pending, start=1):
            try:
                result = client.fetch(link.tmdb_id)
            except TmdbAccessDeniedError as exc:
                stats.stopped_early = True
                stats.stop_reason = str(exc)
                logger.error("%s", exc)
                break
            except TmdbIngestError as exc:
                stats.failed += 1
                stats.failed_ids.append(link.tmdb_id)
                consecutive_failures += 1
                logger.warning("tmdb %s (movies %s): %s", link.tmdb_id, link.movie_ids, exc)
                if consecutive_failures >= max_consecutive_failures:
                    stats.stopped_early = True
                    stats.stop_reason = (
                        f"{consecutive_failures} consecutive failures — "
                        "stopping and checkpointing rather than hammering TMDB"
                    )
                    logger.error("%s", stats.stop_reason)
                    break
                continue

            consecutive_failures = 0
            if result.status == "ok":
                stats.ok += 1
            else:
                stats.not_found += 1
            writer.write(
                build_record(
                    movie_ids=link.movie_ids,
                    result=result,
                    fetched_at=now().isoformat(timespec="seconds").replace("+00:00", "Z"),
                )
            )

            if progress_every and index % progress_every == 0:
                elapsed = time.monotonic() - started
                rate = index / elapsed if elapsed > 0 else 0.0
                remaining = (len(pending) - index) / rate if rate > 0 else 0.0
                logger.info(
                    "%d/%d fetched (%d ok, %d 404, %d failed) — %.1f/s, ~%.0f min left",
                    index,
                    len(pending),
                    stats.ok,
                    stats.not_found,
                    stats.failed,
                    rate,
                    remaining / 60.0,
                )
    finally:
        # Closing inside `finally` is what makes a Ctrl-C a checkpoint rather
        # than a loss: the in-flight shard is flushed and renamed on the way out.
        writer.close()

    return stats, writer.shards


def write_manifest(
    *,
    out_dir: Path,
    pull_date: str,
    stats: PullStats,
    client: TmdbDetailClient,
    catalog_movies: int,
    movies_with_tmdb_id: int,
    distinct_tmdb_ids: int,
    started_at: datetime,
    finished_at: datetime,
    configured_rate: float,
) -> Path:
    """Write (or extend) the manifest that describes this snapshot.

    Shards are re-read from disk rather than taken from the run that just
    happened, so a manifest written by a resumed run describes the whole
    snapshot instead of the tail of it. Per-run counters go into ``runs``, which
    is append-only — the record of how a snapshot was actually assembled is part
    of what makes it reproducible.
    """
    path = out_dir / MANIFEST_NAME
    existing: dict[str, Any] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded

    shards = [_describe_shard(shard, _count_records(shard)) for shard in shard_paths(out_dir)]
    runs: list[dict[str, Any]] = list(existing.get("runs", []))
    runs.append(
        {
            "started_at": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "finished_at": finished_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "wall_clock_seconds": round((finished_at - started_at).total_seconds(), 1),
            "configured_requests_per_second": configured_rate,
            "final_requests_per_second": client.rate_per_second,
            "rate_reductions": client.rate_reductions,
            "requested": stats.requested,
            "skipped_already_present": stats.skipped,
            "total_requests": client.requests_sent,
            "ok": stats.ok,
            "not_found": stats.not_found,
            "failed": stats.failed,
            "throttled_responses": client.throttled_responses,
            "server_error_responses": client.server_error_responses,
            "stopped_early": stats.stopped_early,
            "stop_reason": stats.stop_reason,
            "failed_ids": stats.failed_ids[:500],
        }
    )

    manifest = {
        "pull_date": pull_date,
        "api_version": TMDB_API_VERSION,
        "endpoint": "/movie/{id}",
        "append_to_response": APPEND_TO_RESPONSE,
        "language": LANGUAGE,
        "images_appended": False,
        "catalog_movies": catalog_movies,
        "movies_with_tmdb_id": movies_with_tmdb_id,
        "distinct_tmdb_ids": distinct_tmdb_ids,
        "records": sum(shard.records for shard in shards),
        "shards": [
            {
                "name": shard.name,
                "records": shard.records,
                "bytes": shard.bytes,
                "sha256": shard.sha256,
            }
            for shard in shards
        ],
        "runs": runs,
    }
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _count_records(path: Path) -> int:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def default_out_dir(settings: Settings, pull_date: str) -> Path:
    return settings.raw_data_dir / "tmdb" / pull_date


def read_access_token() -> str:
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        raise TmdbIngestError(
            f"{TOKEN_ENV_VAR} is not set. Export the TMDB v4 read access token before "
            "running the pull; it is never read from the repository."
        )
    return token


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Snapshot TMDB detail payloads for the MovieLens catalog.",
    )
    parser.add_argument(
        "--links-csv",
        type=Path,
        default=None,
        help="MovieLens links.csv (default: <raw_data_dir>/ml-25m/links.csv).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where the shards go (default: <raw_data_dir>/tmdb/<pull-date>).",
    )
    parser.add_argument(
        "--pull-date",
        default=date.today().isoformat(),
        help="Snapshot date, used as the directory name. Defaults to today.",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_REQUESTS_PER_SECOND,
        help=(
            "Requests per second. TMDB guides ~40-50; the default of 20 stays well "
            "under it and the client halves itself further if it still sees 429s."
        ),
    )
    parser.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Fetch at most this many ids. For smoke runs.",
    )
    parser.add_argument(
        "--max-consecutive-failures", type=int, default=DEFAULT_MAX_CONSECUTIVE_FAILURES
    )
    parser.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY)
    args = parser.parse_args(argv)

    settings = Settings()
    links_csv = args.links_csv or (settings.raw_data_dir / "ml-25m" / "links.csv")
    if not links_csv.exists():
        raise SystemExit(f"{links_csv} not found — run `make data-download` (or `dvc pull`) first.")
    out_dir = args.out_dir or default_out_dir(settings, args.pull_date)

    links = read_links(links_csv)
    catalog_movies = sum(1 for _ in _read_movie_ids(links_csv))
    movies_with_tmdb_id = sum(len(link.movie_ids) for link in links)
    logger.info(
        "%d catalog movies, %d with a tmdbId over %d distinct TMDB ids — "
        "snapshotting into %s at %.1f req/s",
        catalog_movies,
        movies_with_tmdb_id,
        len(links),
        out_dir,
        args.rate,
    )

    token = read_access_token()
    bucket = TokenBucket(rate_per_second=args.rate)
    started_at = datetime.now(UTC)
    with httpx.Client(
        timeout=REQUEST_TIMEOUT_SECONDS,
        # One connection pool for the whole run. Reconnecting per request would
        # put a TLS handshake inside every one of 62,000 fetches.
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
        headers={"User-Agent": "movielens-recsys/tmdb-ingest"},
    ) as http_client:
        client = TmdbDetailClient(
            read_access_token=token,
            client=http_client,
            bucket=bucket,
            api_base_url=settings.tmdb_api_base_url,
        )
        stats, _ = run_pull(
            links=links,
            client=client,
            out_dir=out_dir,
            shard_size=args.shard_size,
            limit=args.limit,
            max_consecutive_failures=args.max_consecutive_failures,
            progress_every=args.progress_every,
        )
    finished_at = datetime.now(UTC)

    manifest = write_manifest(
        out_dir=out_dir,
        pull_date=args.pull_date,
        stats=stats,
        client=client,
        catalog_movies=catalog_movies,
        movies_with_tmdb_id=movies_with_tmdb_id,
        distinct_tmdb_ids=len(links),
        started_at=started_at,
        finished_at=finished_at,
        configured_rate=args.rate,
    )
    logger.info(
        "Done: %d ok, %d not found, %d failed, %d requests in %.1f min. Manifest: %s",
        stats.ok,
        stats.not_found,
        stats.failed,
        client.requests_sent,
        (finished_at - started_at).total_seconds() / 60.0,
        manifest,
    )
    logger.info("Now version the snapshot: dvc add %s && dvc push", out_dir)
    return 1 if stats.stopped_early else 0


def _read_movie_ids(links_csv: Path) -> Iterator[int]:
    with links_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                yield int(row["movieId"])
            except (KeyError, TypeError, ValueError):
                continue


if __name__ == "__main__":
    raise SystemExit(main())
