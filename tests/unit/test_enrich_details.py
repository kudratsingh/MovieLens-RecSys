"""The offline detail-enrichment pass, with TMDB stubbed out.

Nothing here touches the network: the pure projection is exercised against
recorded payload shapes, and the run loop against an ``httpx.MockTransport``.
The one test that reads the committed fixture asserts its shape offline, which
is the half of the freshness question CI is allowed to answer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from synthetic.personas.enrich_details import (
    BACKDROP_SIZE,
    DETAILS_FIELD,
    MAX_CAST_MEMBERS,
    PROFILE_SIZE,
    build_details,
    details_of,
    enrich,
    image_url_from_path,
    image_url_shape_error,
    main,
    select_cast,
    select_directors,
    select_targets,
    select_trailer,
)
from synthetic.personas.enrich_posters import (
    CATALOG_PATH,
    TmdbCatalogClient,
    parse_catalog,
)

_FIXTURE = """{
  "movies": [
    {"movie_id": 1, "title": "Toy Story (1995)", "genres": "Animation", \
"tmdb_id": "862", "poster_url": "https://image.tmdb.org/t/p/w500/toy.jpg", \
"overview": "A cowboy doll feels threatened."},
    {"movie_id": 2, "title": "Jumanji (1995)", "genres": "Adventure", \
"tmdb_id": "8844", "poster_url": "https://image.tmdb.org/t/p/w500/jum.jpg"},
    {"movie_id": 3, "title": "Nameless (1995)", "genres": "Drama"}
  ],
  "background_user_ids": [900000201]
}
"""

_TOY_STORY: dict[str, Any] = {
    "tagline": "  Hang on for the comedy that goes to infinity and beyond.  ",
    "runtime": 81,
    "release_date": "1995-10-30",
    "backdrop_path": "/toy-backdrop.jpg",
    "vote_average": 7.968,
    "vote_count": 18_000,
    "credits": {
        "cast": [
            {"name": "Tim Allen", "character": "Buzz", "profile_path": "/ta.jpg", "order": 1},
            {"name": "Tom Hanks", "character": "Woody", "profile_path": "/th.jpg", "order": 0},
        ],
        "crew": [
            {"name": "Ralph Guggenheim", "job": "Producer"},
            {"name": "John Lasseter", "job": "Director"},
            {"name": "John Lasseter", "job": "Director"},
        ],
    },
    "videos": {
        "results": [
            {
                "site": "YouTube",
                "type": "Teaser",
                "key": "teaser-key",
                "name": "Teaser",
                "official": True,
                "iso_639_1": "en",
            },
            {
                "site": "Vimeo",
                "type": "Trailer",
                "key": "vimeo-key",
                "name": "Elsewhere",
                "official": True,
            },
            {
                "site": "YouTube",
                "type": "Trailer",
                "key": "trailer-key",
                "name": "Official Trailer",
                "official": True,
                "iso_639_1": "en",
            },
        ]
    },
}

_JUMANJI: dict[str, Any] = {
    "tagline": "",
    "runtime": 0,
    "release_date": "not-a-date",
    "backdrop_path": None,
    "vote_average": 0.0,
    "vote_count": 0,
    "credits": {"cast": [], "crew": []},
    "videos": {"results": []},
}


def _handler(request: httpx.Request) -> httpx.Response:
    """Stand in for TMDB: both ids answer, one of them with nothing to say."""
    assert request.headers["Authorization"] == "Bearer test-token"
    assert request.url.params.get("append_to_response") == "videos,credits"
    path = request.url.path
    if path == "/3/movie/862":
        return httpx.Response(200, json=_TOY_STORY)
    if path == "/3/movie/8844":
        return httpx.Response(200, json=_JUMANJI)
    raise AssertionError(f"unexpected request: {path}")


def _client(handler: Any, **kwargs: Any) -> TmdbCatalogClient:
    """A paced client whose clock and sleep never touch real time."""
    return TmdbCatalogClient(
        read_access_token="test-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _seconds: None,
        monotonic=lambda: 0.0,
        **kwargs,
    )


def _clock(moment: str) -> Any:
    return lambda: datetime.fromisoformat(moment).replace(tzinfo=UTC)


def _movies_by_id(text: str) -> dict[int, dict[str, Any]]:
    payload = json.loads(text)
    return {int(movie["movie_id"]): movie for movie in payload["movies"]}


def test_the_projection_is_exactly_the_pinned_contract() -> None:
    details = build_details(_TOY_STORY, fetched_at="2026-08-28T00:00:00+00:00")

    assert details == {
        "tagline": "Hang on for the comedy that goes to infinity and beyond.",
        "runtime_minutes": 81,
        "release_date": "1995-10-30",
        "backdrop_url": "https://image.tmdb.org/t/p/w1280/toy-backdrop.jpg",
        "tmdb_rating": {"average": 8.0, "count": 18_000},
        "directors": ["John Lasseter"],
        "cast": [
            {
                "name": "Tom Hanks",
                "character": "Woody",
                "profile_url": "https://image.tmdb.org/t/p/w185/th.jpg",
            },
            {
                "name": "Tim Allen",
                "character": "Buzz",
                "profile_url": "https://image.tmdb.org/t/p/w185/ta.jpg",
            },
        ],
        "trailer": {"provider": "youtube", "key": "trailer-key", "name": "Official Trailer"},
        "fetched_at": "2026-08-28T00:00:00+00:00",
    }
    # Key order is part of the contract: the fixture is reviewed as a diff.
    assert list(details) == [
        "tagline",
        "runtime_minutes",
        "release_date",
        "backdrop_url",
        "tmdb_rating",
        "directors",
        "cast",
        "trailer",
        "fetched_at",
    ]


def test_a_title_tmdb_knows_nothing_about_yields_nulls_not_zeroes() -> None:
    """Every absent value is null, never a rendered 0 min or a 0.0 score."""
    details = build_details(_JUMANJI, fetched_at="2026-08-28T00:00:00+00:00")

    assert details["tagline"] is None
    assert details["runtime_minutes"] is None
    assert details["release_date"] is None
    assert details["backdrop_url"] is None
    assert details["tmdb_rating"] is None
    assert details["directors"] == []
    assert details["cast"] == []
    assert details["trailer"] is None


def test_the_trailer_ranking_prefers_an_official_youtube_trailer() -> None:
    assert select_trailer(_TOY_STORY["videos"]) == {
        "provider": "youtube",
        "key": "trailer-key",
        "name": "Official Trailer",
    }
    # A teaser is the fallback when there is no trailer, and an unofficial
    # trailer still beats an official teaser.
    only_teaser = {"results": [_TOY_STORY["videos"]["results"][0]]}
    assert select_trailer(only_teaser) == {
        "provider": "youtube",
        "key": "teaser-key",
        "name": "Teaser",
    }
    assert select_trailer({"results": [_TOY_STORY["videos"]["results"][1]]}) is None
    assert select_trailer({}) is None


def test_a_trailer_key_that_is_not_a_plain_youtube_id_is_refused() -> None:
    """The key is interpolated into an embed URL, so it is validated here."""
    for key in ("../../evil", "abc?autoplay=1", "a b", "", "x" * 65):
        videos = {"results": [{"site": "YouTube", "type": "Trailer", "key": key, "name": "n"}]}
        assert select_trailer(videos) is None


def test_cast_follows_billing_order_and_stops_at_six() -> None:
    credits = {
        "cast": [
            {"name": f"Actor {index}", "character": "", "order": 10 - index} for index in range(10)
        ]
        + [{"name": "Unbilled", "character": "Extra"}]
    }

    cast = select_cast(credits)

    assert len(cast) == MAX_CAST_MEMBERS
    assert [member["name"] for member in cast] == [f"Actor {index}" for index in range(9, 3, -1)]
    # TMDB's empty character string is "we do not know", not a blank role.
    assert all(member["character"] is None for member in cast)
    assert all(member["profile_url"] is None for member in cast)


def test_directors_are_de_duplicated_in_crew_order() -> None:
    assert select_directors(_TOY_STORY["credits"]) == ["John Lasseter"]
    assert select_directors({"crew": [{"name": "A", "job": "Gaffer"}]}) == []
    assert select_directors({}) == []


def test_image_urls_are_pinned_to_one_size_each() -> None:
    assert (
        image_url_from_path("/a.jpg", size=BACKDROP_SIZE)
        == "https://image.tmdb.org/t/p/w1280/a.jpg"
    )
    assert (
        image_url_from_path("/a.jpg", size=PROFILE_SIZE) == "https://image.tmdb.org/t/p/w185/a.jpg"
    )
    assert image_url_from_path("a.jpg", size=BACKDROP_SIZE) is None
    assert image_url_from_path("/../secret.jpg", size=BACKDROP_SIZE) is None
    assert image_url_from_path(None, size=BACKDROP_SIZE) is None
    assert image_url_shape_error("https://images.example/a.jpg", size=BACKDROP_SIZE) is not None
    assert (
        image_url_shape_error("https://image.tmdb.org/t/p/w500/a.jpg", size=BACKDROP_SIZE)
        is not None
    )
    assert image_url_shape_error("https://image.tmdb.org/t/p/w1280/a.jpg?x=1", size=BACKDROP_SIZE)


def test_only_missing_is_the_default_and_skips_a_title_with_no_tmdb_id() -> None:
    document = parse_catalog(_FIXTURE)

    summary = enrich(document, _client(_handler), refresh=False, now=_clock("2026-08-28T00:00:00"))

    assert summary.filled == [1, 2]
    assert summary.skipped_no_tmdb_id == [3]
    assert summary.no_trailer == [2]
    assert summary.no_cast == [2]
    movies = _movies_by_id(document.render())
    assert movies[1][DETAILS_FIELD]["trailer"]["key"] == "trailer-key"
    assert DETAILS_FIELD not in movies[3]


def test_details_land_last_and_leave_every_other_line_untouched() -> None:
    document = parse_catalog(_FIXTURE)

    enrich(document, _client(_handler), refresh=False, only={1}, now=_clock("2026-08-28T00:00:00"))
    rendered = document.render()

    assert list(_movies_by_id(rendered)[1]) == [
        "movie_id",
        "title",
        "genres",
        "tmdb_id",
        "poster_url",
        "overview",
        DETAILS_FIELD,
    ]
    for line in _FIXTURE.splitlines():
        if '"movie_id": 1' not in line:
            assert line in rendered.splitlines()


def test_a_second_run_changes_nothing() -> None:
    document = parse_catalog(_FIXTURE)
    enrich(document, _client(_handler), refresh=False, now=_clock("2026-08-28T00:00:00"))
    first = document.render()

    second = parse_catalog(first)
    summary = enrich(second, _client(_handler), refresh=False, now=_clock("2026-09-01T00:00:00"))

    assert summary.filled == []
    assert summary.changed is False
    assert sorted(summary.unchanged) == [1, 2]
    assert second.render() == first


def test_a_refresh_that_learns_nothing_keeps_the_timestamp_it_had() -> None:
    """The property that makes ``--refresh`` safe to run on a cadence."""
    document = parse_catalog(_FIXTURE)
    enrich(document, _client(_handler), refresh=False, now=_clock("2026-08-28T00:00:00"))
    first = document.render()

    second = parse_catalog(first)
    summary = enrich(second, _client(_handler), refresh=True, now=_clock("2026-09-01T00:00:00"))

    assert summary.changed is False
    assert second.render() == first
    assert _movies_by_id(first)[1][DETAILS_FIELD]["fetched_at"] == "2026-08-28T00:00:00+00:00"


def test_a_refresh_writes_the_field_that_actually_moved() -> None:
    document = parse_catalog(_FIXTURE)
    enrich(document, _client(_handler), refresh=False, now=_clock("2026-08-28T00:00:00"))
    moved = dict(_TOY_STORY, runtime=92)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3/movie/862":
            return httpx.Response(200, json=moved)
        return _handler(request)

    summary = enrich(
        parse_catalog(document.render()),
        _client(handler),
        refresh=True,
        only={1},
        now=_clock("2026-09-01T00:00:00"),
    )

    assert summary.updated == [1]
    assert summary.changed is True


def test_a_persistent_upstream_failure_is_an_error_not_a_silent_skip() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "0"})

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=False, only={1})

    assert [movie_id for movie_id, _ in summary.errors] == [1]
    assert summary.changed is False
    assert "503" in summary.render()


def test_an_id_tmdb_does_not_know_is_reported_and_written_nowhere() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status_code": 34})

    document = parse_catalog(_FIXTURE)
    summary = enrich(document, _client(handler), refresh=False, only={1})

    assert summary.unknown_to_tmdb == [1]
    assert DETAILS_FIELD not in _movies_by_id(document.render())[1]


def test_dry_run_makes_no_requests_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "catalog.json"
    fixture.write_text(_FIXTURE, encoding="utf-8")
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "unused")

    def refuse(*_args: Any, **_kwargs: Any) -> httpx.Client:
        raise AssertionError("a dry run must not open a client")

    monkeypatch.setattr(httpx, "Client", refuse)
    exit_code = main(["--dry-run", "--catalog", str(fixture)])

    assert exit_code == 0
    assert "2 of 3 entries would be fetched" in capsys.readouterr().out
    assert fixture.read_text(encoding="utf-8") == _FIXTURE


def test_main_refuses_to_run_without_a_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "catalog.json"
    fixture.write_text(_FIXTURE, encoding="utf-8")
    monkeypatch.delenv("TMDB_READ_ACCESS_TOKEN", raising=False)

    assert main(["--catalog", str(fixture)]) == 2
    assert "TMDB_READ_ACCESS_TOKEN" in capsys.readouterr().err


def test_main_exits_non_zero_on_a_request_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "catalog.json"
    fixture.write_text(_FIXTURE, encoding="utf-8")
    monkeypatch.setenv("TMDB_READ_ACCESS_TOKEN", "test-token")
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *_args, **_kwargs: real_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        ),
    )

    assert main(["--catalog", str(fixture)]) == 1
    assert fixture.read_text(encoding="utf-8") == _FIXTURE


def test_every_committed_details_object_matches_the_contract() -> None:
    """The offline half of the freshness check, so it can gate CI.

    Liveness — does that backdrop still resolve, is that trailer key still on
    YouTube — deliberately stays out of CI for the same reason poster liveness
    does: a third party's uptime must not decide whether a PR can merge.
    """
    document = parse_catalog(CATALOG_PATH.read_text(encoding="utf-8"))
    enriched = [entry for entry in document.entries if details_of(entry) is not None]

    assert len(enriched) == len(document.entries), "every fixture title carries a details object"
    for entry in enriched:
        details = details_of(entry)
        assert details is not None
        assert list(details) == [
            "tagline",
            "runtime_minutes",
            "release_date",
            "backdrop_url",
            "tmdb_rating",
            "directors",
            "cast",
            "trailer",
            "fetched_at",
        ], entry.movie_id
        if details["backdrop_url"] is not None:
            assert image_url_shape_error(details["backdrop_url"], size=BACKDROP_SIZE) is None
        assert len(details["cast"]) <= MAX_CAST_MEMBERS
        for member in details["cast"]:
            assert member["name"]
            if member["profile_url"] is not None:
                assert image_url_shape_error(member["profile_url"], size=PROFILE_SIZE) is None
        trailer = details["trailer"]
        if trailer is not None:
            assert trailer["provider"] == "youtube"
            assert trailer["key"] and " " not in trailer["key"]
        datetime.fromisoformat(str(details["fetched_at"]))


def test_select_targets_never_reaches_a_title_without_an_id() -> None:
    document = parse_catalog(_FIXTURE)

    assert [entry.movie_id for entry in select_targets(document.entries, refresh=True)] == [1, 2]
    assert [
        entry.movie_id for entry in select_targets(document.entries, refresh=True, only={2, 3})
    ] == [2]
