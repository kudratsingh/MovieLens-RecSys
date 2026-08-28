from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from synthetic.personas.enrich_posters import (
    CATALOG_PATH,
    CatalogFormatError,
    Summary,
    TmdbCatalogClient,
    TmdbRequestError,
    enrich,
    main,
    parse_catalog,
    poster_url_from_path,
    select_targets,
    split_title_and_year,
)

_FIXTURE = """{
  "movies": [
    {"movie_id": 1, "title": "Toy Story (1995)", "genres": "Animation", \
"tmdb_id": "862", "poster_url": "https://image.tmdb.org/t/p/w500/old.jpg", \
"overview": "A cowboy doll feels threatened."},

    {"movie_id": 2, "title": "Jumanji (1995)", "genres": "Adventure"},
    {"movie_id": 3, "title": "Ghostless (1995)", "genres": "Drama"}
  ],
  "background_user_ids": [900000201]
}
"""


def _client(handler: Any, **kwargs: Any) -> TmdbCatalogClient:
    """A paced client whose clock and sleep never touch real time."""
    return TmdbCatalogClient(
        read_access_token="test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
        **kwargs,
    )


def _movies_by_id(text: str) -> dict[int, dict[str, Any]]:
    payload = json.loads(text)
    return {int(movie["movie_id"]): movie for movie in payload["movies"]}


def _handler(request: httpx.Request) -> httpx.Response:
    """Stand in for TMDB: id 862 has a poster, Jumanji resolves by search."""
    assert request.headers["Authorization"] == "Bearer test-token"
    path = request.url.path
    if path == "/3/movie/862":
        return httpx.Response(200, json={"poster_path": "/new.jpg"})
    if path == "/3/search/movie":
        query = request.url.params.get("query")
        if query == "Jumanji":
            assert request.url.params.get("primary_release_year") == "1995"
            return httpx.Response(
                200,
                json={"results": [{"id": 8844, "title": "Jumanji", "poster_path": "/jum.jpg"}]},
            )
        return httpx.Response(200, json={"results": []})
    raise AssertionError(f"unexpected request: {path}")


def test_poster_url_uses_the_committed_w500_shape() -> None:
    assert poster_url_from_path("/abc.jpg") == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert poster_url_from_path(None) is None
    assert poster_url_from_path("abc.jpg") is None
    assert poster_url_from_path("/../secret.jpg") is None


def test_committed_fixture_round_trips_byte_for_byte() -> None:
    text = CATALOG_PATH.read_text(encoding="utf-8")

    document = parse_catalog(text)

    assert document.render() == text
    assert len(document.entries) == 120


def test_parse_refuses_a_fixture_it_would_reformat() -> None:
    reformatted = _FIXTURE.replace(
        '    {"movie_id": 2, "title": "Jumanji (1995)", "genres": "Adventure"},',
        '    {"movie_id":2,"title":"Jumanji (1995)","genres":"Adventure"},',
    )

    with pytest.raises(CatalogFormatError):
        parse_catalog(reformatted)


def test_split_title_and_year_reads_the_movielens_suffix() -> None:
    assert split_title_and_year("Jumanji (1995)") == ("Jumanji", 1995)
    assert split_title_and_year("Untitled") == ("Untitled", None)


def test_only_missing_is_the_default_and_leaves_existing_posters_alone() -> None:
    document = parse_catalog(_FIXTURE)

    assert [entry.movie_id for entry in select_targets(document.entries, refresh=False)] == [2, 3]

    summary = enrich(document, _client(_handler), refresh=False)
    movies = _movies_by_id(document.render())

    assert summary.filled == [2]
    assert summary.already_had == [1]
    assert summary.unresolved == [3]
    assert movies[1]["poster_url"] == "https://image.tmdb.org/t/p/w500/old.jpg"
    assert movies[2]["poster_url"] == "https://image.tmdb.org/t/p/w500/jum.jpg"
    assert movies[2]["tmdb_id"] == "8844"
    assert "poster_url" not in movies[3]


def test_new_fields_land_in_the_canonical_key_order() -> None:
    document = parse_catalog(_FIXTURE)

    enrich(document, _client(_handler), refresh=False)
    movies = _movies_by_id(document.render())

    assert list(movies[2]) == ["movie_id", "title", "genres", "tmdb_id", "poster_url"]
    assert list(movies[1]) == ["movie_id", "title", "genres", "tmdb_id", "poster_url", "overview"]


def test_untouched_lines_keep_their_exact_formatting() -> None:
    document = parse_catalog(_FIXTURE)

    enrich(document, _client(_handler), refresh=False)
    before = _FIXTURE.split("\n")
    after = document.render().split("\n")

    assert len(before) == len(after)
    changed = [index for index, line in enumerate(after) if line != before[index]]
    assert changed == [4]
    # The blank line that separates the enriched block survives a rewrite.
    assert after[3] == ""


def test_refresh_overwrites_an_existing_poster() -> None:
    document = parse_catalog(_FIXTURE)

    summary = enrich(document, _client(_handler), refresh=True)
    movies = _movies_by_id(document.render())

    assert summary.refreshed == [1]
    assert summary.filled == [2]
    assert movies[1]["poster_url"] == "https://image.tmdb.org/t/p/w500/new.jpg"


def test_a_second_run_changes_nothing() -> None:
    document = parse_catalog(_FIXTURE)
    enrich(document, _client(_handler), refresh=False)
    enriched = document.render()

    reloaded = parse_catalog(enriched)
    summary = enrich(reloaded, _client(_handler), refresh=False)

    assert reloaded.render() == enriched
    assert summary.filled == []
    assert summary.changed is False


def test_a_title_tmdb_has_no_poster_for_keeps_no_poster_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/search/movie":
            return httpx.Response(
                200,
                json={"results": [{"id": 8844, "title": "Jumanji", "poster_path": None}]},
            )
        return httpx.Response(200, json={"poster_path": "/new.jpg"})

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=False)
    movies = _movies_by_id(document.render())

    assert summary.no_poster == [2, 3]
    assert "poster_url" not in movies[2]
    # The resolved id is still worth keeping: the next run is a plain lookup.
    assert movies[2]["tmdb_id"] == "8844"


def test_a_non_exact_search_match_is_reported_for_review() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"id": 8844, "title": "Jumanji: Welcome", "poster_path": "/j.jpg"}]},
        )

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=False)

    assert summary.fuzzy_matches == [
        (2, "Jumanji (1995)", "8844"),
        (3, "Ghostless (1995)", "8844"),
    ]
    assert "review (matched by year and search rank" in summary.render(dry_run=False)


def test_a_missing_tmdb_id_falls_back_to_an_exact_match_without_the_year() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("primary_release_year") is not None:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={"results": [{"id": 8844, "title": "Jumanji", "poster_path": "/jum.jpg"}]},
        )

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=False)

    assert summary.filled == [2]
    assert summary.fuzzy_matches == []


def test_one_retry_honours_retry_after_before_giving_up() -> None:
    attempts: list[str] = []
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={})
        return httpx.Response(200, json={"poster_path": "/new.jpg"})

    client = TmdbCatalogClient(
        read_access_token="test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=waits.append,
        monotonic=lambda: 0.0,
    )

    assert client.movie("862") == {"poster_path": "/new.jpg"}
    assert len(attempts) == 2
    assert 2.0 in waits


def test_a_persistent_failure_is_an_error_not_a_silent_skip() -> None:
    document = parse_catalog(_FIXTURE)

    summary = enrich(
        document,
        _client(lambda request: httpx.Response(503, json={})),
        refresh=False,
    )

    assert [movie_id for movie_id, _ in summary.errors] == [2, 3]
    assert summary.changed is False
    assert "errors: 2" in summary.render(dry_run=False)


def test_a_transport_failure_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(TmdbRequestError):
        _client(handler).movie("862")


def test_summary_render_reports_every_bucket() -> None:
    summary = Summary(filled=[2], already_had=[1], no_poster=[3], unresolved=[4])

    rendered = summary.render(dry_run=True)

    assert "would fill: 1" in rendered
    assert "already had a poster: 1" in rendered
    assert "no poster on TMDB: 1" in rendered
    assert "unresolved on TMDB: 1" in rendered


def test_main_refuses_to_run_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)

    assert main(["--dry-run"]) == 2
    assert "TMDB_READ_ACCESS_TOKEN is not set" in capsys.readouterr().err


def test_dry_run_makes_no_requests_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *args, **kwargs: pytest.fail("a dry run must not open an HTTP client"),
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(_FIXTURE, encoding="utf-8")

    assert main(["--dry-run", "--catalog", str(catalog)]) == 0
    assert catalog.read_text(encoding="utf-8") == _FIXTURE
    assert "2 of 3 entries would be fetched" in capsys.readouterr().out
