"""Offline detail enrichment for the reviewed demo catalog fixture.

The movie detail page had nothing on it that Browse did not already have: a
poster, a year, two genres and a two-line synopsis. Everything a viewer opens a
detail page *for* — the trailer, who directed it, who is in it, how long it
runs, what the crowd scored it — lives on TMDB and was never brought across.
This is the offline pass that brings it across, into the same fixture-owned
snapshot the posters came from (``docs/frontend/catalog-contract.md``): the
request path still never fans out to TMDB, and a detail page still renders from
one local row.

Re-run it when the fixture gains titles, or when a trailer key has gone stale::

    set -a && . /path/to/tmdb.env && set +a     # TMDB_READ_ACCESS_TOKEN
    python -m synthetic.personas.enrich_details --dry-run
    python -m synthetic.personas.enrich_details
    python -m synthetic.personas.enrich_details --refresh --only 1,6,10

One request per title — ``append_to_response=videos,credits`` folds the cast,
the crew and the video list into the same payload, so 120 titles cost 120
requests rather than 360. The token is read from ``TMDB_READ_ACCESS_TOKEN`` and
from nowhere else; it is never written into the repository and the FastAPI
service does not need it, exactly as with the poster pass.

``--only-missing`` (the default) fills entries that have no ``details`` object.
``--refresh`` re-fetches every entry, and is still diff-clean: a re-fetch that
comes back identical keeps the ``fetched_at`` it already had, so a run that
learned nothing writes nothing. That is the property that makes this safe to
run on a cadence — the alternative, stamping a new timestamp on 120 lines every
time, would make a real change impossible to spot in the diff.

What lands is deliberately narrow. A trailer key is accepted only if it is a
plain YouTube id: the web app interpolates it into a ``youtube-nocookie.com``
embed URL, so anything else in that field would be a URL the fixture gets to
write into the product's markup. Backdrop and profile paths are pinned to their
one image size each for the same reason the poster URL is (``w1280`` and
``w185``), and the offline shape check rides along in ``tests/unit`` where it
gates CI. The liveness half is not covered for these images — ``make
catalog-verify`` HEADs posters only — because a missing backdrop degrades to
the poster-framed layout the page already has, where a missing poster does not
degrade to anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from synthetic.personas.enrich_posters import (
    CATALOG_PATH,
    IMAGE_BASE_URL,
    REQUEST_TIMEOUT_SECONDS,
    CatalogDocument,
    CatalogEntry,
    TmdbCatalogClient,
    TmdbRequestError,
    parse_catalog,
)

DETAILS_FIELD = "details"

# One size each, pinned the way the poster URL is. The browser will only load
# what ``web/next.config.ts`` allows, so a URL in another size is a URL that
# renders as a hole in the page rather than as a smaller image.
BACKDROP_SIZE = "w1280"
PROFILE_SIZE = "w185"

# Six is what the design contract's cast row shows, and TMDB returns billing
# order, so the truncation is the top of the bill rather than an arbitrary six.
MAX_CAST_MEMBERS = 6

# The single append the detail pass needs. Credits carry cast and crew; videos
# carry the trailer.
APPEND_TO_RESPONSE = "videos,credits"

_IMAGE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9]+$")
# YouTube ids are unreserved URL characters only. This field ends up inside an
# embed URL in the product, so it is validated where it is written rather than
# trusted at the point of use.
_YOUTUBE_KEY = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
_RELEASE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TRAILER_TYPES = ("Trailer", "Teaser")


def image_url_from_path(image_path: object, *, size: str) -> str | None:
    """Build a pinned-size TMDB image URL, rejecting anything unusable."""
    if not isinstance(image_path, str):
        return None
    if not image_path.startswith("/") or ".." in image_path:
        return None
    url = f"{IMAGE_BASE_URL}/{size}{image_path}"
    return None if image_url_shape_error(url, size=size) else url


def image_url_shape_error(url: object, *, size: str) -> str | None:
    """Say why an image URL is not the pinned shape, or ``None`` if it is.

    The twin of ``poster_url_shape_error`` for the two sizes the detail object
    carries. Offline on purpose: it catches a hand-edited host, a different
    image size or a query string smuggled onto the path without asking a third
    party anything, which is what lets it gate CI.
    """
    prefix = f"{IMAGE_BASE_URL}/{size}/"
    if not isinstance(url, str) or not url:
        return "no image url"
    if not url.startswith(prefix):
        return f"not the pinned {prefix}<file> shape"
    filename = url[len(prefix) :]
    if not _IMAGE_FILENAME.fullmatch(filename):
        return f"image path {filename!r} is not a plain image file name"
    return None


def _clean_text(value: object) -> str | None:
    """Collapse TMDB's whitespace, treating its empty string as "none"."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    return collapsed or None


def _runtime_minutes(value: object) -> int | None:
    # TMDB uses 0 for "we do not know", which would render as a 0 min runtime.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _release_date(value: object) -> str | None:
    if not isinstance(value, str) or not _RELEASE_DATE.fullmatch(value):
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _tmdb_rating(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The crowd score, or ``None`` when nobody has voted.

    An average with no votes behind it is 0.0, and a 0.0 rendered next to a
    star is a claim the data does not make.
    """
    average = payload.get("vote_average")
    count = payload.get("vote_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    if isinstance(average, bool) or not isinstance(average, (int, float)):
        return None
    return {"average": round(float(average), 1), "count": count}


def select_directors(credits: dict[str, Any]) -> list[str]:
    """Directors in crew order, de-duplicated (a co-credit can repeat)."""
    crew = credits.get("crew")
    if not isinstance(crew, list):
        return []
    directors: list[str] = []
    for member in crew:
        if not isinstance(member, dict) or member.get("job") != "Director":
            continue
        name = _clean_text(member.get("name"))
        if name is not None and name not in directors:
            directors.append(name)
    return directors


def _billing_order(member: dict[str, Any]) -> int:
    """TMDB's billing position, with an unbilled member sorted to the back."""
    order = member.get("order")
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        return 1_000_000
    return order


def select_cast(credits: dict[str, Any]) -> list[dict[str, Any]]:
    """The top of the billing, at most ``MAX_CAST_MEMBERS`` deep.

    TMDB returns billing order in ``order``; sorting explicitly rather than
    trusting the array's order is what keeps two runs over the same title from
    producing two different top sixes.
    """
    cast = credits.get("cast")
    if not isinstance(cast, list):
        return []
    ranked = sorted(
        (member for member in cast if isinstance(member, dict)),
        key=_billing_order,
    )
    members: list[dict[str, Any]] = []
    for member in ranked:
        name = _clean_text(member.get("name"))
        if name is None:
            continue
        members.append(
            {
                "name": name,
                "character": _clean_text(member.get("character")),
                "profile_url": image_url_from_path(member.get("profile_path"), size=PROFILE_SIZE),
            }
        )
        if len(members) == MAX_CAST_MEMBERS:
            break
    return members


def select_trailer(videos: dict[str, Any]) -> dict[str, Any] | None:
    """Pick one YouTube trailer, preferring an official full trailer.

    Ranking rather than "first match" because TMDB's list is not ordered by
    usefulness: a title can carry six teasers, a clip and one official trailer,
    and the official trailer is the one a viewer means by "play trailer".
    """
    results = videos.get("results")
    if not isinstance(results, list):
        return None
    candidates: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for video in results:
        if not isinstance(video, dict) or video.get("site") != "YouTube":
            continue
        video_type = video.get("type")
        if video_type not in _TRAILER_TYPES:
            continue
        key = video.get("key")
        if not isinstance(key, str) or not _YOUTUBE_KEY.fullmatch(key):
            continue
        name = _clean_text(video.get("name")) or f"{video_type}"
        rank = (
            _TRAILER_TYPES.index(video_type),
            0 if video.get("official") is True else 1,
            0 if video.get("iso_639_1") == "en" else 1,
        )
        candidates.append((rank, {"provider": "youtube", "key": key, "name": name}))
    if not candidates:
        return None
    # ``min`` over a stable list keeps ties resolved by TMDB's own order, so the
    # same payload always yields the same trailer.
    return min(candidates, key=lambda candidate: candidate[0])[1]


def build_details(payload: dict[str, Any], *, fetched_at: str) -> dict[str, Any]:
    """Project one TMDB payload onto the fixture's ``details`` contract.

    Pure, and the only place the contract's key order is written down: the
    fixture is read as a diff, so the object's shape has to be identical from
    one title to the next.
    """
    credits = payload.get("credits")
    videos = payload.get("videos")
    return {
        "tagline": _clean_text(payload.get("tagline")),
        "runtime_minutes": _runtime_minutes(payload.get("runtime")),
        "release_date": _release_date(payload.get("release_date")),
        "backdrop_url": image_url_from_path(payload.get("backdrop_path"), size=BACKDROP_SIZE),
        "tmdb_rating": _tmdb_rating(payload),
        "directors": select_directors(credits if isinstance(credits, dict) else {}),
        "cast": select_cast(credits if isinstance(credits, dict) else {}),
        "trailer": select_trailer(videos if isinstance(videos, dict) else {}),
        "fetched_at": fetched_at,
    }


def details_of(entry: CatalogEntry) -> dict[str, Any] | None:
    value = entry.data.get(DETAILS_FIELD)
    return value if isinstance(value, dict) else None


def select_targets(
    entries: Sequence[CatalogEntry],
    *,
    refresh: bool,
    only: Collection[int] | None = None,
) -> list[CatalogEntry]:
    """Pick the entries a run will fetch.

    A title with no ``tmdb_id`` is never a target: resolving one by title
    search is the poster pass's job, and a detail run that also guessed at
    identity would be two decisions in one diff.
    """
    selected = [
        entry
        for entry in entries
        if entry.tmdb_id is not None and (refresh or details_of(entry) is None)
    ]
    if only is None:
        return selected
    return [entry for entry in selected if entry.movie_id in only]


def _comparable(details: dict[str, Any]) -> str:
    """The details object minus the timestamp, as a comparable string."""
    return json.dumps(
        {key: value for key, value in details.items() if key != "fetched_at"},
        sort_keys=True,
    )


@dataclass
class Summary:
    filled: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    unchanged: list[int] = field(default_factory=list)
    skipped_no_tmdb_id: list[int] = field(default_factory=list)
    unknown_to_tmdb: list[int] = field(default_factory=list)
    no_trailer: list[int] = field(default_factory=list)
    no_cast: list[int] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.filled or self.updated)

    def render(self) -> str:
        lines = [
            f"filled: {len(self.filled)}",
            f"updated: {len(self.updated)}",
            f"already current: {len(self.unchanged)}",
            f"skipped, no tmdb_id: {len(self.skipped_no_tmdb_id)}",
            f"unknown to TMDB: {len(self.unknown_to_tmdb)}",
            f"no trailer on TMDB: {len(self.no_trailer)}",
            f"no cast on TMDB: {len(self.no_cast)}",
            f"errors: {len(self.errors)}",
        ]
        if self.skipped_no_tmdb_id:
            lines.append(f"  no tmdb_id: {_format_ids(self.skipped_no_tmdb_id)}")
        if self.unknown_to_tmdb:
            lines.append(f"  unknown to TMDB: {_format_ids(self.unknown_to_tmdb)}")
        if self.no_trailer:
            lines.append(f"  no trailer: {_format_ids(self.no_trailer)}")
        if self.no_cast:
            lines.append(f"  no cast: {_format_ids(self.no_cast)}")
        lines.extend(f"  error: {movie_id}: {message}" for movie_id, message in self.errors)
        return "\n".join(lines)


def _format_ids(movie_ids: Sequence[int]) -> str:
    return ", ".join(str(movie_id) for movie_id in movie_ids)


def enrich(
    document: CatalogDocument,
    client: TmdbCatalogClient,
    *,
    refresh: bool,
    only: Collection[int] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Summary:
    """Fill ``details`` objects in place, reporting every entry's outcome."""
    summary = Summary()
    targets = select_targets(document.entries, refresh=refresh, only=only)
    target_ids = {id(entry) for entry in targets}
    summary.skipped_no_tmdb_id.extend(
        entry.movie_id
        for entry in document.entries
        if entry.tmdb_id is None and (only is None or entry.movie_id in only)
    )

    for entry in targets:
        tmdb_id = entry.tmdb_id
        if tmdb_id is None:  # pragma: no cover - select_targets filters these out
            continue
        try:
            payload = client.movie(tmdb_id, append_to_response=APPEND_TO_RESPONSE)
        except TmdbRequestError as exc:
            summary.errors.append((entry.movie_id, str(exc)))
            continue
        if payload is None:
            summary.unknown_to_tmdb.append(entry.movie_id)
            continue

        details = build_details(payload, fetched_at=_timestamp(now))
        if details["trailer"] is None:
            summary.no_trailer.append(entry.movie_id)
        if not details["cast"]:
            summary.no_cast.append(entry.movie_id)

        existing = details_of(entry)
        if existing is None:
            entry.set_fields(details=details)
            summary.filled.append(entry.movie_id)
            continue
        if _comparable(existing) == _comparable(details):
            # Nothing upstream moved. Keeping the stored timestamp is what makes
            # a refresh diff-clean: a run that learned nothing writes nothing.
            summary.unchanged.append(entry.movie_id)
            continue
        entry.set_fields(details=details)
        summary.updated.append(entry.movie_id)

    # Every entry that was in scope but not fetched: reported rather than silent.
    summary.unchanged.extend(
        entry.movie_id
        for entry in document.entries
        if id(entry) not in target_ids
        and entry.tmdb_id is not None
        and details_of(entry) is not None
        and (only is None or entry.movie_id in only)
    )
    return summary


def _timestamp(now: Callable[[], datetime]) -> str:
    return now().astimezone(UTC).replace(microsecond=0).isoformat()


def _parse_only(value: str | None) -> set[int] | None:
    if value is None:
        return None
    ids = {int(part) for part in value.replace(",", " ").split()}
    if not ids:
        raise argparse.ArgumentTypeError("--only needs at least one movie id")
    return ids


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m synthetic.personas.enrich_details",
        description=(
            "Fill the reviewed demo catalog fixture's detail objects — tagline, "
            "runtime, release date, backdrop, crowd score, directors, billed "
            "cast and one YouTube trailer — from TMDB."
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
        help="fill entries that carry no details object (the default)",
    )
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch every entry and rewrite the ones whose details moved",
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
    targets = select_targets(document.entries, refresh=args.refresh, only=args.only)

    if args.dry_run:
        # A dry run makes no requests at all, so it stays usable as a cheap
        # "is there anything left to do?" check against the committed fixture.
        print(f"{len(targets)} of {len(document.entries)} entries would be fetched from TMDB.")
        if targets:
            print(f"  {_format_ids([entry.movie_id for entry in targets])}")
        return 0

    token = os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
    if not token:
        print("TMDB_READ_ACCESS_TOKEN is not set; export it and re-run.", file=sys.stderr)
        return 2

    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
        client = TmdbCatalogClient(read_access_token=token, client=http_client)
        summary = enrich(document, client, refresh=args.refresh, only=args.only)

    if summary.changed:
        catalog_path.write_text(document.render(), encoding="utf-8")
    print(summary.render())
    print(f"{'wrote' if summary.changed else 'left unchanged'} {catalog_path}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
