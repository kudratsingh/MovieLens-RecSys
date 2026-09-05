"""The TMDB pull's rate limiting, backoff, resume and shard writing.

No network anywhere in here. The HTTP layer is a fake that returns whatever the
test scripted, and the clock is a counter the test advances by hand — a rate
limiter tested by actually sleeping is a rate limiter nobody runs in CI, and a
backoff tested against wall-clock time is a flaky test waiting to happen.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.data.tmdb_ingest import (
    APPEND_TO_RESPONSE,
    CatalogLink,
    FetchResult,
    ShardWriter,
    TmdbAccessDeniedError,
    TmdbDetailClient,
    TmdbIngestError,
    TokenBucket,
    build_record,
    completed_tmdb_ids,
    discard_partial_shards,
    read_links,
    read_shard_records,
    run_pull,
    write_manifest,
)


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _response(status: int, *, body: str = "{}", headers: dict[str, str] | None = None):
    request = httpx.Request("GET", "https://api.themoviedb.org/3/movie/1")
    return httpx.Response(status, text=body, headers=headers or {}, request=request)


class FakeHttp:
    """Replays a scripted list of responses (or exceptions) and records calls."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        if not self._script:
            raise AssertionError(f"unscripted request to {url}")
        nxt = self._script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _client(script: list[Any], clock: FakeClock, **kwargs: Any) -> TmdbDetailClient:
    bucket = TokenBucket(
        rate_per_second=kwargs.pop("rate", 20.0),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return TmdbDetailClient(
        read_access_token="test-token",
        client=FakeHttp(script),  # type: ignore[arg-type]
        bucket=bucket,
        sleep=clock.sleep,
        rng=lambda: 0.5,
        **kwargs,
    )


# --- the bucket -------------------------------------------------------------


def test_bucket_admits_a_burst_then_paces_at_the_configured_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate_per_second=10.0, monotonic=clock.monotonic, sleep=clock.sleep)

    # The bucket starts full, so the first ten cost nothing.
    assert [bucket.take() for _ in range(10)] == [0.0] * 10
    # The eleventh has to wait for one token to refill: 1/10 of a second.
    assert bucket.take() == pytest.approx(0.1)
    assert clock.slept == [pytest.approx(0.1)]


def test_bucket_never_exceeds_its_average_over_a_long_run() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate_per_second=20.0, monotonic=clock.monotonic, sleep=clock.sleep)
    start = clock.now
    for _ in range(200):
        bucket.take()
    elapsed = clock.now - start
    # 200 requests, 20 up front for free, so 180 paced at 1/20s.
    assert elapsed == pytest.approx(9.0)


def test_bucket_rejects_a_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=0.0)


# --- retries and backoff ----------------------------------------------------


def test_a_throttled_request_honours_retry_after_then_succeeds() -> None:
    clock = FakeClock()
    client = _client(
        [_response(429, headers={"Retry-After": "7"}), _response(200, body='{"id":1}')],
        clock,
    )

    result = client.fetch(1)

    assert result.status == "ok"
    assert result.attempts == 2
    # Retry-After plus the jittering term, which the fixed rng pins at 0.5.
    assert clock.slept[-1] == pytest.approx(7.5)
    assert client.throttled_responses == 1


def test_a_server_error_backs_off_exponentially() -> None:
    clock = FakeClock()
    client = _client([_response(503), _response(503), _response(200)], clock)

    client.fetch(1)

    # Base 1s doubling, each scaled by 0.5 + 0.5 * rng() with rng pinned to 0.5.
    assert clock.slept == [pytest.approx(0.75), pytest.approx(1.5)]
    assert client.server_error_responses == 2


def test_a_transport_error_is_retried_like_a_server_error() -> None:
    clock = FakeClock()
    client = _client([httpx.ConnectError("boom"), _response(200)], clock)

    assert client.fetch(1).status == "ok"


def test_a_404_is_an_answer_rather_than_a_failure() -> None:
    clock = FakeClock()
    client = _client([_response(404)], clock)

    result = client.fetch(1)

    assert result.status == "not_found"
    assert result.raw is None


def test_a_rejected_token_stops_the_run_instead_of_retrying() -> None:
    clock = FakeClock()
    client = _client([_response(401)], clock)

    with pytest.raises(TmdbAccessDeniedError):
        client.fetch(1)
    # Nothing slept, nothing retried — the whole point.
    assert clock.slept == []


def test_the_attempt_budget_is_finite() -> None:
    clock = FakeClock()
    client = _client([_response(500)] * 4, clock, max_attempts=4)

    with pytest.raises(TmdbIngestError):
        client.fetch(1)


def test_sustained_throttling_halves_the_rate() -> None:
    clock = FakeClock()
    client = _client(
        [_response(429) for _ in range(3)] + [_response(200)],
        clock,
        rate=20.0,
        throttle_tolerance=2,
        max_attempts=6,
    )

    client.fetch(1)

    assert client.rate_reductions == 1
    assert client.rate_per_second == pytest.approx(10.0)


def test_the_request_carries_the_bearer_token_and_the_appends() -> None:
    clock = FakeClock()
    client = _client([_response(200)], clock)

    client.fetch(862)

    call = client._client.calls[0]  # type: ignore[attr-defined]
    assert call["url"].endswith("/movie/862")
    assert call["params"]["append_to_response"] == APPEND_TO_RESPONSE
    assert "images" not in call["params"]["append_to_response"]
    assert call["headers"]["Authorization"] == "Bearer test-token"


# --- shards -----------------------------------------------------------------


def test_the_shard_writer_rotates_and_only_renames_complete_shards(tmp_path: Path) -> None:
    writer = ShardWriter(tmp_path, shard_size=2)
    writer.write('{"a":1}')
    writer.write('{"a":2}')
    # Two records in, the first shard is closed and named; the third opens a new
    # partial that has not been renamed yet.
    writer.write('{"a":3}')

    assert (tmp_path / "movies-00000.jsonl.gz").exists()
    assert (tmp_path / "movies-00001.jsonl.gz.partial").exists()
    assert not (tmp_path / "movies-00001.jsonl.gz").exists()

    writer.close()
    assert (tmp_path / "movies-00001.jsonl.gz").exists()
    assert not list(tmp_path.glob("*.partial"))
    assert [info.records for info in writer.shards] == [2, 1]


def test_shards_are_byte_identical_across_runs_with_the_same_content(tmp_path: Path) -> None:
    """The snapshot is DVC-tracked, so a re-write of the same data must not churn."""
    first, second = tmp_path / "a", tmp_path / "b"
    for target in (first, second):
        writer = ShardWriter(target, shard_size=10)
        writer.write('{"a":1}')
        writer.close()
    assert (first / "movies-00000.jsonl.gz").read_bytes() == (
        second / "movies-00000.jsonl.gz"
    ).read_bytes()


def test_a_record_embeds_the_response_body_verbatim() -> None:
    raw = '{"id":862,"title":"Toy Story","popularity":21.946}'
    line = build_record(
        movie_ids=[1],
        result=FetchResult(tmdb_id=862, status="ok", raw=raw, attempts=1),
        fetched_at="2026-09-05T09:00:00Z",
    )

    # Byte-for-byte, not a re-serialization: the substring is literally present.
    assert raw in line
    parsed = json.loads(line)
    assert parsed["movie_ids"] == [1]
    assert parsed["payload"]["title"] == "Toy Story"


def test_a_pretty_printed_body_is_compacted_so_one_record_stays_one_line() -> None:
    line = build_record(
        movie_ids=[1],
        result=FetchResult(tmdb_id=862, status="ok", raw='{\n  "id": 862\n}', attempts=1),
        fetched_at="2026-09-05T09:00:00Z",
    )

    assert "\n" not in line
    assert json.loads(line)["payload"] == {"id": 862}


def test_a_not_found_record_carries_a_null_payload() -> None:
    line = build_record(
        movie_ids=[1, 2],
        result=FetchResult(tmdb_id=862, status="not_found", raw=None, attempts=1),
        fetched_at="2026-09-05T09:00:00Z",
    )

    parsed = json.loads(line)
    assert parsed["payload"] is None
    assert parsed["movie_ids"] == [1, 2]


def test_partial_shards_are_discarded_and_completed_ids_come_from_the_rest(
    tmp_path: Path,
) -> None:
    writer = ShardWriter(tmp_path, shard_size=1)
    writer.write(json.dumps({"tmdb_id": 11, "movie_ids": [1], "status": "ok", "payload": {}}))
    writer.write(json.dumps({"tmdb_id": 22, "movie_ids": [2], "status": "not_found"}))
    # Leave the second shard unrenamed, as a killed run would.
    (tmp_path / "movies-00002.jsonl.gz.partial").write_bytes(b"truncated")

    assert discard_partial_shards(tmp_path) == 1
    # A 404 counts as done — re-asking would just get the same 404.
    assert completed_tmdb_ids(tmp_path) == {11, 22}


# --- links ------------------------------------------------------------------


def test_links_are_grouped_by_tmdb_id_so_a_duplicate_costs_one_request(
    tmp_path: Path,
) -> None:
    links_csv = tmp_path / "links.csv"
    links_csv.write_text(
        "movieId,imdbId,tmdbId\n1,0114709,862\n7,0113228,15602\n9,0114885,\n40,0113101,862\n",
        encoding="utf-8",
    )

    links = read_links(links_csv)

    # 862 is claimed by movies 1 and 40 — one entry, two movie ids. The blank
    # tmdbId is dropped rather than turned into a request that cannot succeed.
    assert links == [
        CatalogLink(tmdb_id=862, movie_ids=(1, 40)),
        CatalogLink(tmdb_id=15602, movie_ids=(7,)),
    ]


# --- the pull ---------------------------------------------------------------


def test_a_resumed_pull_skips_what_is_already_on_disk(tmp_path: Path) -> None:
    clock = FakeClock()
    links = [CatalogLink(tmdb_id=11, movie_ids=(1,)), CatalogLink(tmdb_id=22, movie_ids=(2,))]

    first = _client([_response(200, body='{"id":11}')], clock)
    run_pull(links=links[:1], client=first, out_dir=tmp_path, shard_size=10, progress_every=0)

    second = _client([_response(200, body='{"id":22}')], clock)
    stats, _ = run_pull(
        links=links, client=second, out_dir=tmp_path, shard_size=10, progress_every=0
    )

    assert stats.skipped == 1
    assert stats.requested == 1
    assert stats.ok == 1
    assert completed_tmdb_ids(tmp_path) == {11, 22}


def test_a_re_run_over_a_finished_snapshot_sends_no_requests(tmp_path: Path) -> None:
    clock = FakeClock()
    links = [CatalogLink(tmdb_id=11, movie_ids=(1,))]
    run_pull(
        links=links,
        client=_client([_response(200, body='{"id":11}')], clock),
        out_dir=tmp_path,
        shard_size=10,
        progress_every=0,
    )

    idempotent = _client([], clock)  # an unscripted request would raise
    stats, _ = run_pull(
        links=links, client=idempotent, out_dir=tmp_path, shard_size=10, progress_every=0
    )

    assert stats.requested == 0
    assert idempotent.requests_sent == 0


def test_consecutive_failures_stop_the_run_and_keep_what_was_fetched(tmp_path: Path) -> None:
    clock = FakeClock()
    links = [CatalogLink(tmdb_id=i, movie_ids=(i,)) for i in range(1, 6)]
    # One success, then nothing but 500s: two ids fail their whole attempt
    # budget, which trips a max_consecutive_failures of 2.
    script = [_response(200, body='{"id":1}')] + [_response(500)] * 8
    client = _client(script, clock, max_attempts=4)

    stats, _ = run_pull(
        links=links,
        client=client,
        out_dir=tmp_path,
        shard_size=10,
        max_consecutive_failures=2,
        progress_every=0,
    )

    assert stats.stopped_early is True
    assert stats.failed == 2
    assert stats.ok == 1
    # The successful record survived the stop — that is what makes it a
    # checkpoint rather than a loss.
    assert completed_tmdb_ids(tmp_path) == {1}


def test_a_rejected_token_ends_the_pull_without_burning_the_rest(tmp_path: Path) -> None:
    clock = FakeClock()
    links = [CatalogLink(tmdb_id=i, movie_ids=(i,)) for i in range(1, 5)]
    client = _client([_response(401)], clock)

    stats, _ = run_pull(
        links=links, client=client, out_dir=tmp_path, shard_size=10, progress_every=0
    )

    assert stats.stopped_early is True
    assert client.requests_sent == 1


def test_a_duplicated_tmdb_id_lands_as_one_record_naming_both_movies(tmp_path: Path) -> None:
    clock = FakeClock()
    client = _client([_response(200, body='{"id":862}')], clock)

    run_pull(
        links=[CatalogLink(tmdb_id=862, movie_ids=(1, 40))],
        client=client,
        out_dir=tmp_path,
        shard_size=10,
        progress_every=0,
    )

    records = list(read_shard_records(tmp_path))
    assert len(records) == 1
    assert records[0]["movie_ids"] == [1, 40]
    assert client.requests_sent == 1


def test_the_manifest_records_the_pull_and_hashes_every_shard(tmp_path: Path) -> None:
    from datetime import datetime

    clock = FakeClock()
    client = _client([_response(200, body='{"id":11}'), _response(404)], clock)
    stats, _ = run_pull(
        links=[CatalogLink(tmdb_id=11, movie_ids=(1,)), CatalogLink(tmdb_id=22, movie_ids=(2,))],
        client=client,
        out_dir=tmp_path,
        shard_size=10,
        progress_every=0,
    )

    started = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
    path = write_manifest(
        out_dir=tmp_path,
        pull_date="2026-09-05",
        stats=stats,
        client=client,
        catalog_movies=62_423,
        movies_with_tmdb_id=62_316,
        distinct_tmdb_ids=62_282,
        started_at=started,
        finished_at=started,
        configured_rate=20.0,
    )

    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["pull_date"] == "2026-09-05"
    assert manifest["api_version"] == "3"
    assert manifest["images_appended"] is False
    assert manifest["records"] == 2
    assert len(manifest["shards"]) == 1
    assert len(manifest["shards"][0]["sha256"]) == 64
    run = manifest["runs"][0]
    assert (run["ok"], run["not_found"], run["failed"]) == (1, 1, 0)
    assert run["total_requests"] == 2
    # The token must never appear in an artifact this repo could publish.
    assert "test-token" not in path.read_text(encoding="utf-8")


def test_a_second_run_appends_to_the_manifest_rather_than_replacing_it(tmp_path: Path) -> None:
    from datetime import datetime

    clock = FakeClock()
    started = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
    for tmdb_id in (11, 22):
        client = _client([_response(200, body=json.dumps({"id": tmdb_id}))], clock)
        stats, _ = run_pull(
            links=[CatalogLink(tmdb_id=tmdb_id, movie_ids=(tmdb_id,))],
            client=client,
            out_dir=tmp_path,
            shard_size=10,
            progress_every=0,
        )
        path = write_manifest(
            out_dir=tmp_path,
            pull_date="2026-09-05",
            stats=stats,
            client=client,
            catalog_movies=62_423,
            movies_with_tmdb_id=62_316,
            distinct_tmdb_ids=62_282,
            started_at=started,
            finished_at=started,
            configured_rate=20.0,
        )

    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert len(manifest["runs"]) == 2
    # `shards` is re-read from disk, so it describes the whole snapshot rather
    # than only what the last run happened to write.
    assert manifest["records"] == 2


def test_shard_records_round_trip_through_gzip(tmp_path: Path) -> None:
    writer = ShardWriter(tmp_path, shard_size=10)
    writer.write(json.dumps({"tmdb_id": 1, "movie_ids": [1], "status": "ok", "payload": {"a": 1}}))
    writer.close()

    with gzip.open(tmp_path / "movies-00000.jsonl.gz", "rt", encoding="utf-8") as handle:
        assert json.loads(handle.read())["payload"] == {"a": 1}
