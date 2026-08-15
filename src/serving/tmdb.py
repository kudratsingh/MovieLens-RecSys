"""Resilient server-side TMDB movie metadata client."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TmdbMovieMetadata:
    poster_url: str | None
    overview: str | None
    release_year: int | None


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    value: TmdbMovieMetadata | None


class TmdbMetadataClient:
    """Fetch TMDB details without making the recommendation path depend on TMDB."""

    def __init__(
        self,
        *,
        read_access_token: str | None,
        api_base_url: str = "https://api.themoviedb.org/3",
        image_base_url: str = "https://image.tmdb.org/t/p",
        timeout_seconds: float = 2.0,
        cache_ttl_seconds: int = 21_600,
        cache_max_entries: int = 2_048,
        max_concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        stripped_token = read_access_token.strip() if read_access_token else ""
        self._token = stripped_token or None
        self._api_base_url = api_base_url.rstrip("/")
        self._image_base_url = image_base_url.rstrip("/")
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._clock = clock
        self._cache: OrderedDict[int, _CacheEntry] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def enabled(self) -> bool:
        return self._token is not None

    async def get_many(self, tmdb_ids: Iterable[str | None]) -> dict[str, TmdbMovieMetadata]:
        """Resolve unique valid IDs concurrently; failures are omitted."""
        normalized = sorted(
            {tmdb_id for value in tmdb_ids if (tmdb_id := _normalize_tmdb_id(value)) is not None}
        )
        if not self.enabled or not normalized:
            return {}
        results = await asyncio.gather(*(self._get_one(tmdb_id) for tmdb_id in normalized))
        return {
            str(tmdb_id): metadata
            for tmdb_id, metadata in zip(normalized, results, strict=True)
            if metadata is not None
        }

    async def _get_one(self, tmdb_id: int) -> TmdbMovieMetadata | None:
        cached, found = await self._get_cached(tmdb_id)
        if found:
            return cached

        async with self._semaphore:
            try:
                response = await self._client.get(
                    f"{self._api_base_url}/movie/{tmdb_id}",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                    params={"language": "en-US"},
                )
                if response.status_code == 404:
                    metadata = None
                else:
                    response.raise_for_status()
                    metadata = self._parse_metadata(response.json())
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                logger.warning(
                    "TMDB metadata unavailable for movie_id=%s error=%s",
                    tmdb_id,
                    type(exc).__name__,
                )
                metadata = None

        await self._store_cached(tmdb_id, metadata)
        return metadata

    def _parse_metadata(self, payload: Any) -> TmdbMovieMetadata:
        if not isinstance(payload, dict):
            raise TypeError("TMDB movie response is not an object")
        poster_path = payload.get("poster_path")
        poster_url = None
        if isinstance(poster_path, str) and poster_path.startswith("/") and ".." not in poster_path:
            poster_url = f"{self._image_base_url}/w500{poster_path}"

        overview_value = payload.get("overview")
        overview = overview_value.strip() if isinstance(overview_value, str) else None
        if not overview:
            overview = None

        release_date = payload.get("release_date")
        release_year = None
        if isinstance(release_date, str) and len(release_date) >= 4:
            year = release_date[:4]
            release_year = int(year) if year.isdigit() else None

        return TmdbMovieMetadata(
            poster_url=poster_url,
            overview=overview,
            release_year=release_year,
        )

    async def _get_cached(self, tmdb_id: int) -> tuple[TmdbMovieMetadata | None, bool]:
        async with self._cache_lock:
            entry = self._cache.get(tmdb_id)
            if entry is None:
                return None, False
            if entry.expires_at <= self._clock():
                del self._cache[tmdb_id]
                return None, False
            self._cache.move_to_end(tmdb_id)
            return entry.value, True

    async def _store_cached(self, tmdb_id: int, value: TmdbMovieMetadata | None) -> None:
        async with self._cache_lock:
            self._cache[tmdb_id] = _CacheEntry(
                expires_at=self._clock() + self._cache_ttl_seconds,
                value=value,
            )
            self._cache.move_to_end(tmdb_id)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _normalize_tmdb_id(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None
