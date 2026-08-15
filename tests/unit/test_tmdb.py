from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from src.serving.tmdb import TmdbMetadataClient


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_no_token_skips_upstream_requests() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500)

    client = _client(handler)
    tmdb = TmdbMetadataClient(read_access_token="   ", client=client)

    assert await tmdb.get_many(["11"]) == {}
    assert request_count == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_movie_details_build_w500_poster_and_use_bearer_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer server-secret"
        assert request.url.path == "/3/movie/11"
        assert request.url.params["language"] == "en-US"
        return httpx.Response(
            200,
            json={
                "poster_path": "/poster.jpg",
                "overview": "  Space opera.  ",
                "release_date": "1977-05-25",
            },
        )

    client = _client(handler)
    tmdb = TmdbMetadataClient(read_access_token="server-secret", client=client)

    result = await tmdb.get_many(["11"])

    assert result["11"].poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert result["11"].overview == "Space opera."
    assert result["11"].release_year == 1977
    await client.aclose()


@pytest.mark.asyncio
async def test_missing_poster_keeps_other_metadata() -> None:
    client = _client(
        lambda request: httpx.Response(
            200,
            json={"poster_path": None, "overview": "Drama", "release_date": "1994"},
        )
    )
    tmdb = TmdbMetadataClient(read_access_token="token", client=client)

    result = await tmdb.get_many(["278"])

    assert result["278"].poster_url is None
    assert result["278"].overview == "Drama"
    assert result["278"].release_year == 1994
    await client.aclose()


@pytest.mark.asyncio
async def test_timeout_degrades_to_empty_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream timeout", request=request)

    client = _client(handler)
    tmdb = TmdbMetadataClient(read_access_token="token", client=client)

    assert await tmdb.get_many(["11"]) == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_success_and_failure_results_are_cached() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request.url.path.endswith("/404"):
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={"poster_path": "/cached.jpg", "overview": "", "release_date": ""},
        )

    client = _client(handler)
    tmdb = TmdbMetadataClient(read_access_token="token", client=client)

    first = await tmdb.get_many(["11", "404", "11"])
    second = await tmdb.get_many(["11", "404"])

    assert (
        first
        == second
        == {
            "11": first["11"],
        }
    )
    assert request_count == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_cache_expires_and_invalid_ids_are_ignored() -> None:
    now = 100.0
    request_count = 0

    def clock() -> float:
        return now

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={"poster_path": "/poster.jpg", "overview": "", "release_date": ""},
        )

    client = _client(handler)
    tmdb = TmdbMetadataClient(
        read_access_token="token",
        client=client,
        cache_ttl_seconds=10,
        clock=clock,
    )

    await tmdb.get_many([None, "", "abc", "0", "11"])
    now = 111.0
    await tmdb.get_many(["11"])

    assert request_count == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_cache_evicts_least_recently_used_entry_when_bounded() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={"poster_path": "/poster.jpg", "overview": "", "release_date": ""},
        )

    client = _client(handler)
    tmdb = TmdbMetadataClient(
        read_access_token="token",
        client=client,
        cache_max_entries=1,
    )

    await tmdb.get_many(["11"])
    await tmdb.get_many(["12"])
    await tmdb.get_many(["11"])

    assert request_count == 3
    await client.aclose()
