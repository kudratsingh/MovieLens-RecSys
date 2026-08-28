from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from synthetic.personas.enrich_posters import (
    CATALOG_PATH,
    CatalogFormatError,
    PosterVerifier,
    Summary,
    TmdbCatalogClient,
    TmdbRequestError,
    enrich,
    main,
    parse_catalog,
    poster_url_from_path,
    poster_url_shape_error,
    select_targets,
    split_title_and_year,
    verify_catalog,
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


def _verifier(handler: Any, **kwargs: Any) -> PosterVerifier:
    return PosterVerifier(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
        **kwargs,
    )


def _mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Make ``main`` open a mocked client, holding on to the real constructor."""
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *args, **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )


def _image_response(
    request: httpx.Request,
    *,
    dead: frozenset[str] = frozenset(),
) -> httpx.Response:
    """Stand in for the image CDN, which is a different host with no token."""
    assert request.method == "HEAD"
    assert "Authorization" not in request.headers
    assert request.url.host == "image.tmdb.org"
    return httpx.Response(404 if request.url.path.rsplit("/", 1)[-1] in dead else 200)


def _movies_by_id(text: str) -> dict[int, dict[str, Any]]:
    payload = json.loads(text)
    return {int(movie["movie_id"]): movie for movie in payload["movies"]}


def _handler(request: httpx.Request) -> httpx.Response:
    """Stand in for TMDB: id 862 has a poster, Jumanji resolves by search."""
    if request.url.host == "image.tmdb.org":
        return _image_response(request)
    assert request.headers["Authorization"] == "Bearer test-token"
    path = request.url.path
    if path == "/3/movie/862":
        return httpx.Response(200, json={"poster_path": "/new.jpg"})
    if path == "/3/movie/8844":
        # The id the first run learned. TMDB has no synopsis for it, which is
        # what keeps it a target on every later run without changing any bytes.
        return httpx.Response(200, json={"poster_path": "/jum.jpg", "overview": ""})
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


# --- The liveness gate: nothing is written before it is checked ---------------


def test_a_poster_the_image_host_will_not_serve_is_reported_not_written() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "image.tmdb.org":
            return _image_response(request, dead=frozenset({"jum.jpg"}))
        return _handler(request)

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=False)
    movies = _movies_by_id(document.render())

    assert summary.dead_posters == [(2, "HTTP 404")]
    assert summary.filled == []
    assert "poster_url" not in movies[2]
    # The id is still worth keeping — only the artwork was unusable.
    assert movies[2]["tmdb_id"] == "8844"
    assert "dead upstream: 2: HTTP 404" in summary.render(dry_run=False)


def test_a_refresh_keeps_the_poster_it_has_when_the_new_one_is_dead() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "image.tmdb.org":
            return _image_response(request, dead=frozenset({"new.jpg"}))
        return _handler(request)

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=True)
    movies = _movies_by_id(document.render())

    assert summary.refreshed == []
    assert summary.dead_posters == [(1, "HTTP 404")]
    assert movies[1]["poster_url"] == "https://image.tmdb.org/t/p/w500/old.jpg"


def test_a_dead_poster_makes_the_run_exit_non_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "image.tmdb.org":
            return _image_response(request, dead=frozenset({"jum.jpg"}))
        return _handler(request)

    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "test-token")
    _mock_transport(monkeypatch, handler)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(_FIXTURE, encoding="utf-8")

    assert main(["--catalog", str(catalog)]) == 1


def test_a_cdn_hiccup_is_retried_before_a_poster_is_condemned() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        return httpx.Response(200 if len(attempts) > 1 else 503)

    verifier = _verifier(handler)

    assert verifier.check("https://image.tmdb.org/t/p/w500/jum.jpg") is None
    assert len(attempts) == 2


def test_a_transport_failure_leaves_the_poster_unproven() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    assert _verifier(handler).check("https://image.tmdb.org/t/p/w500/jum.jpg") == "request failed"


# --- The offline half of the gate: URL shape ---------------------------------


def test_the_shape_check_pins_the_host_and_the_poster_size() -> None:
    assert poster_url_shape_error("https://image.tmdb.org/t/p/w500/abc.jpg") is None
    assert poster_url_shape_error(None) == "no poster_url"
    assert "pinned" in str(poster_url_shape_error("https://cdn.example.com/w500/abc.jpg"))
    assert "pinned" in str(poster_url_shape_error("https://image.tmdb.org/t/p/w780/abc.jpg"))
    assert "plain image file name" in str(
        poster_url_shape_error("https://image.tmdb.org/t/p/w500/abc.jpg?token=leak")
    )
    assert "plain image file name" in str(
        poster_url_shape_error("https://image.tmdb.org/t/p/w500/nested/abc.jpg")
    )


def test_every_committed_poster_url_has_the_pinned_shape() -> None:
    """The CI-safe half of the liveness gate: no third party is asked anything."""
    document = parse_catalog(CATALOG_PATH.read_text(encoding="utf-8"))

    misshapen = {
        entry.movie_id: poster_url_shape_error(entry.poster_url)
        for entry in document.entries
        if poster_url_shape_error(entry.poster_url) is not None
    }

    assert misshapen == {}


# --- --verify ----------------------------------------------------------------


def test_verify_passes_a_live_fixture_and_names_the_dead_entry() -> None:
    document = parse_catalog(_FIXTURE)
    document.entries[1].set_fields(poster_url="https://image.tmdb.org/t/p/w500/jum.jpg")

    live = verify_catalog(document, _verifier(_image_response))

    assert live.checked == 3
    # Entry 3 was never enriched, so the fixture's own promise is what fails.
    assert [(failure.movie_id, failure.reason) for failure in live.failures] == [
        (3, "no poster_url")
    ]

    dead = verify_catalog(
        document,
        _verifier(lambda request: _image_response(request, dead=frozenset({"old.jpg"}))),
    )

    assert dead.ok is False
    assert [(failure.movie_id, failure.reason) for failure in dead.failures] == [
        (1, "HTTP 404"),
        (3, "no poster_url"),
    ]
    assert "1 'Toy Story (1995)': HTTP 404" in dead.render()


def test_verify_needs_no_token_and_exits_by_what_it_found(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)
    _mock_transport(monkeypatch, _image_response)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(_FIXTURE, encoding="utf-8")

    # Entry 1 is the only one with a stored URL, and it resolves.
    assert main(["--verify", "--only", "1", "--catalog", str(catalog)]) == 0
    assert "checked: 1" in capsys.readouterr().out
    # Entry 3 has none, which is a failure the product would feel.
    assert main(["--verify", "--catalog", str(catalog)]) == 1
    assert catalog.read_text(encoding="utf-8") == _FIXTURE


# --- Overviews ---------------------------------------------------------------


def _overview_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "image.tmdb.org":
        return _image_response(request)
    path = request.url.path
    if path == "/3/movie/862":
        return httpx.Response(
            200,
            json={"poster_path": "/new.jpg", "overview": "TMDB's own longer synopsis."},
        )
    if path == "/3/search/movie":
        if request.url.params.get("query") == "Jumanji":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 8844,
                            "title": "Jumanji",
                            "poster_path": "/jum.jpg",
                            "overview": "  A board game\nreleases a jungle.  ",
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})
    raise AssertionError(f"unexpected request: {path}")


def test_an_overview_lands_in_the_canonical_field_order() -> None:
    document = parse_catalog(_FIXTURE)

    summary = enrich(document, _client(_overview_handler), refresh=False)
    movies = _movies_by_id(document.render())

    assert summary.overviews_filled == [2]
    assert list(movies[2]) == ["movie_id", "title", "genres", "tmdb_id", "poster_url", "overview"]
    # Whitespace is collapsed so one fixture line stays one fixture line.
    assert movies[2]["overview"] == "A board game releases a jungle."


def test_a_reviewed_overview_survives_even_a_refresh() -> None:
    document = parse_catalog(_FIXTURE)

    summary = enrich(document, _client(_overview_handler), refresh=True)
    movies = _movies_by_id(document.render())

    assert movies[1]["overview"] == "A cowboy doll feels threatened."
    assert 1 not in summary.overviews_filled


def test_an_overview_only_run_does_not_churn_the_poster_it_finds() -> None:
    """The 96 synopsis-less titles all had artwork; refetching must not move it."""
    fixture = _FIXTURE.replace(
        ', "overview": "A cowboy doll feels threatened."',
        "",
    )
    document = parse_catalog(fixture)

    summary = enrich(document, _client(_overview_handler), refresh=False)
    movies = _movies_by_id(document.render())

    assert summary.overviews_filled == [1, 2]
    assert summary.refreshed == []
    assert summary.already_had == [1]
    assert movies[1]["poster_url"] == "https://image.tmdb.org/t/p/w500/old.jpg"
    assert movies[1]["overview"] == "TMDB's own longer synopsis."


def test_a_second_run_after_an_overview_landed_changes_nothing() -> None:
    document = parse_catalog(_FIXTURE)
    enrich(document, _client(_overview_handler), refresh=False)
    enriched = document.render()

    reloaded = parse_catalog(enriched)
    summary = enrich(reloaded, _client(_overview_handler), refresh=False)

    assert reloaded.render() == enriched
    assert summary.changed is False


def test_only_narrows_a_refresh_to_the_named_ids() -> None:
    document = parse_catalog(_FIXTURE)

    targets = select_targets(document.entries, refresh=True, only={1})
    summary = enrich(document, _client(_handler), refresh=True, only={1})
    movies = _movies_by_id(document.render())

    assert [entry.movie_id for entry in targets] == [1]
    assert summary.refreshed == [1]
    assert movies[1]["poster_url"] == "https://image.tmdb.org/t/p/w500/new.jpg"
    assert "poster_url" not in movies[2]
