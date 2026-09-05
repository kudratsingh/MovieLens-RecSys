"""The TMDB normaliser and the loader, on a fixture payload. No network.

The fixture is trimmed but structurally faithful: every container the normaliser
reaches into is present in the shape TMDB actually sends it, including the parts
that are awkward — an empty ``release_date`` string, a ``belongs_to_collection``
that is sometimes null, two release entries of the same type for one country,
and an actor billed twice.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import create_engine, select

from src.data.tmdb_ingest import ShardWriter
from src.data.tmdb_load import CREW_JOBS, load_snapshot, normalise_record
from src.data.tmdb_schema import (
    tmdb_keywords,
    tmdb_metadata,
    tmdb_movie_cast,
    tmdb_movie_crew,
    tmdb_movie_genres,
    tmdb_movie_keywords,
    tmdb_movies,
    tmdb_people,
    tmdb_release_dates,
    tmdb_spoken_languages,
)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": 862,
        "adult": False,
        "backdrop_path": "/back.jpg",
        "belongs_to_collection": {"id": 10194, "name": "Toy Story Collection"},
        "budget": 30000000,
        "genres": [{"id": 16, "name": "Animation"}, {"id": 35, "name": "Comedy"}],
        "imdb_id": "tt0114709",
        "original_language": "en",
        "original_title": "Toy Story",
        "overview": "A cowboy doll is profoundly threatened.",
        "popularity": 21.946,
        "poster_path": "/poster.jpg",
        "production_companies": [
            {"id": 3, "name": "Pixar", "origin_country": "US"},
            {"id": 3, "name": "Pixar", "origin_country": "US"},  # TMDB does repeat these
        ],
        "production_countries": [{"iso_3166_1": "US", "name": "United States of America"}],
        "release_date": "1995-10-30",
        "revenue": 373554033,
        "runtime": 81,
        "spoken_languages": [{"english_name": "English", "iso_639_1": "en", "name": "English"}],
        "status": "Released",
        "tagline": "",
        "title": "Toy Story",
        "vote_average": 7.97,
        "vote_count": 17549,
        "keywords": {
            "keywords": [
                {"id": 931, "name": "jealousy"},
                {"id": 4290, "name": "toy"},
            ]
        },
        "credits": {
            "cast": [
                {
                    "id": 31,
                    "name": "Tom Hanks",
                    "original_name": "Tom Hanks",
                    "gender": 2,
                    "known_for_department": "Acting",
                    "profile_path": "/hanks.jpg",
                    "character": "Woody (voice)",
                    "credit_id": "cast-1",
                    "order": 0,
                },
                {
                    "id": 12898,
                    "name": "Tim Allen",
                    "gender": 2,
                    "character": "Buzz Lightyear (voice)",
                    "credit_id": "cast-2",
                    "order": 1,
                },
                # The same actor, billed twice — a real TMDB shape and the
                # reason credit_id is in the key rather than person_id.
                {
                    "id": 31,
                    "name": "Tom Hanks",
                    "character": "Narrator (voice)",
                    "credit_id": "cast-3",
                    "order": 2,
                },
            ],
            "crew": [
                {
                    "id": 7879,
                    "name": "John Lasseter",
                    "department": "Directing",
                    "job": "Director",
                    "credit_id": "crew-1",
                },
                {
                    "id": 7,
                    "name": "Randy Newman",
                    "department": "Sound",
                    "job": "Original Music Composer",
                    "credit_id": "crew-2",
                },
                {
                    "id": 999,
                    "name": "A Gaffer",
                    "department": "Lighting",
                    "job": "Gaffer",
                    "credit_id": "crew-3",
                },
            ],
        },
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {
                            "certification": "G",
                            "iso_639_1": "",
                            "note": "",
                            "release_date": "1995-11-22T00:00:00.000Z",
                            "type": 3,
                        },
                        {
                            "certification": "G",
                            "release_date": "2009-10-02T00:00:00.000Z",
                            "note": "3D re-release",
                            "type": 3,
                        },
                    ],
                }
            ]
        },
        "external_ids": {"imdb_id": "tt0114709", "wikidata_id": "Q171048"},
        "alternative_titles": {"titles": [{"iso_3166_1": "DE", "title": "Toy Story"}]},
        "translations": {"translations": [{"iso_639_1": "de", "name": "Deutsch"}]},
    }
    payload.update(overrides)
    return payload


def _record(*, movie_ids: list[int] | None = None, **overrides: Any) -> dict[str, Any]:
    return {
        "movie_ids": movie_ids if movie_ids is not None else [1],
        "tmdb_id": 862,
        "status": "ok",
        "fetched_at": "2026-09-05T09:00:00Z",
        "payload": _payload(**overrides),
    }


# --- the normaliser ---------------------------------------------------------


def test_the_movie_row_carries_every_scalar_the_schema_declares() -> None:
    (rows,) = normalise_record(_record())

    movie = rows.movie
    assert movie["movie_id"] == 1
    assert movie["tmdb_id"] == 862
    assert movie["title"] == "Toy Story"
    assert movie["release_date"] == date(1995, 10, 30)
    assert movie["runtime"] == 81
    assert movie["original_language"] == "en"
    assert movie["adult"] is False
    assert movie["collection_id"] == 10194
    assert movie["collection_name"] == "Toy Story Collection"
    assert movie["imdb_id"] == "tt0114709"
    assert movie["poster_path"] == "/poster.jpg"
    # An empty tagline is a missing tagline, not the empty string.
    assert movie["tagline"] is None
    assert movie["pulled_at"] is not None


def test_the_as_of_pull_values_are_stored_rather_than_dropped() -> None:
    """They are not features, but the snapshot is a record and it keeps them."""
    (rows,) = normalise_record(_record())

    assert rows.movie["vote_average"] == pytest.approx(7.97)
    assert rows.movie["vote_count"] == 17549
    assert rows.movie["popularity"] == pytest.approx(21.946)
    assert rows.movie["budget"] == 30000000
    assert rows.movie["revenue"] == 373554033
    assert rows.movie["status"] == "Released"


def test_child_rows_are_extracted_and_deduplicated() -> None:
    (rows,) = normalise_record(_record())

    assert {row["genre_name"] for row in rows.genres} == {"Animation", "Comedy"}
    assert {row["keyword_id"] for row in rows.keywords} == {931, 4290}
    assert len(rows.movie_keywords) == 2
    # Pixar appears twice in the payload and once in the rows.
    assert len(rows.companies) == 1
    assert len(rows.movie_companies) == 1
    assert [row["iso_3166_1"] for row in rows.countries] == ["US"]
    assert [row["iso_639_1"] for row in rows.languages] == ["en"]


def test_an_actor_billed_twice_keeps_both_credits_and_one_person_row() -> None:
    (rows,) = normalise_record(_record())

    assert sorted(row["credit_id"] for row in rows.cast) == ["cast-1", "cast-2", "cast-3"]
    assert sum(row["person_id"] == 31 for row in rows.cast) == 2
    assert sum(row["person_id"] == 31 for row in rows.people) == 1


def test_the_cast_is_capped_and_ordered_by_billing() -> None:
    (rows,) = normalise_record(_record(), top_cast=2)

    assert [row["cast_order"] for row in rows.cast] == [0, 1]
    assert [row["character"] for row in rows.cast] == ["Woody (voice)", "Buzz Lightyear (voice)"]


def test_the_cast_slice_does_not_trust_the_payload_ordering() -> None:
    payload = _payload()
    payload["credits"]["cast"] = list(reversed(payload["credits"]["cast"]))

    (rows,) = normalise_record({**_record(), "payload": payload}, top_cast=1)

    assert rows.cast[0]["credit_id"] == "cast-1"


def test_only_the_named_crew_roles_are_kept_by_default() -> None:
    (rows,) = normalise_record(_record())

    assert {row["job"] for row in rows.crew} == {"Director", "Original Music Composer"}
    assert all(row["job"] in CREW_JOBS for row in rows.crew)


def test_all_crew_can_be_kept_when_asked() -> None:
    (rows,) = normalise_record(_record(), crew_jobs=None)

    assert "Gaffer" in {row["job"] for row in rows.crew}


def test_two_release_entries_of_the_same_type_both_survive() -> None:
    (rows,) = normalise_record(_record())

    us = [row for row in rows.release_dates if row["iso_3166_1"] == "US"]
    assert [row["release_index"] for row in us] == [0, 1]
    assert {row["certification"] for row in us} == {"G"}
    assert us[1]["note"] == "3D re-release"


def test_a_duplicated_tmdb_id_produces_one_row_set_per_movielens_movie() -> None:
    rows = normalise_record(_record(movie_ids=[1, 40]))

    assert [row.movie["movie_id"] for row in rows] == [1, 40]
    assert all(row.movie["tmdb_id"] == 862 for row in rows)
    # The child rows are keyed to their own movie, not shared.
    assert {row["movie_id"] for row in rows[1].cast} == {40}


def test_a_not_found_record_produces_nothing() -> None:
    assert normalise_record({"movie_ids": [1], "tmdb_id": 9, "status": "not_found"}) == []


def test_missing_and_empty_fields_become_null_rather_than_empty_strings() -> None:
    (rows,) = normalise_record(
        _record(
            release_date="",
            overview="",
            belongs_to_collection=None,
            runtime=None,
            keywords={"keywords": []},
            credits={},
        )
    )

    assert rows.movie["release_date"] is None
    assert rows.movie["overview"] is None
    assert rows.movie["collection_id"] is None
    assert rows.movie["runtime"] is None
    assert rows.keywords == []
    assert rows.cast == []


def test_a_payload_missing_every_optional_container_does_not_crash() -> None:
    (rows,) = normalise_record(
        {
            "movie_ids": [1],
            "tmdb_id": 862,
            "status": "ok",
            "fetched_at": "2026-09-05T09:00:00Z",
            "payload": {"id": 862, "title": "Bare"},
        }
    )

    assert rows.movie["title"] == "Bare"
    assert (rows.genres, rows.cast, rows.crew, rows.release_dates) == ([], [], [], [])


# --- the loader -------------------------------------------------------------


@pytest.fixture()
def loaded(tmp_path):
    """A SQLite database with one snapshot loaded, plus the shard directory."""
    snapshot = tmp_path / "snapshot"
    writer = ShardWriter(snapshot, shard_size=10)
    writer.write(json.dumps(_record(movie_ids=[1, 40])))
    writer.write(json.dumps({"movie_ids": [2], "tmdb_id": 99, "status": "not_found"}))
    writer.close()

    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    tmdb_metadata.create_all(engine)
    return engine, snapshot


def test_the_loader_writes_every_table(loaded) -> None:
    engine, snapshot = loaded

    stats = load_snapshot(engine, snapshot, progress_every=0)

    assert stats.records_read == 2
    assert stats.not_found_skipped == 1
    assert stats.movies_loaded == 2  # the duplicated tmdb id fans out
    with engine.connect() as connection:
        assert connection.execute(select(tmdb_movies)).rowcount != 0
        movie_ids = [row.movie_id for row in connection.execute(select(tmdb_movies))]
        assert sorted(movie_ids) == [1, 40]
        assert len(list(connection.execute(select(tmdb_movie_genres)))) == 4
        assert len(list(connection.execute(select(tmdb_keywords)))) == 2
        assert len(list(connection.execute(select(tmdb_movie_keywords)))) == 4
        assert len(list(connection.execute(select(tmdb_people)))) == 4
        assert len(list(connection.execute(select(tmdb_movie_cast)))) == 6
        assert len(list(connection.execute(select(tmdb_movie_crew)))) == 4
        assert len(list(connection.execute(select(tmdb_release_dates)))) == 4
        assert len(list(connection.execute(select(tmdb_spoken_languages)))) == 2


def test_loading_twice_leaves_the_same_rows(loaded) -> None:
    """Re-running the loader is the documented fix for a half-finished load."""
    engine, snapshot = loaded

    load_snapshot(engine, snapshot, progress_every=0)
    with engine.connect() as connection:
        before = len(list(connection.execute(select(tmdb_movie_cast))))

    load_snapshot(engine, snapshot, progress_every=0)
    with engine.connect() as connection:
        assert len(list(connection.execute(select(tmdb_movie_cast)))) == before
        assert len(list(connection.execute(select(tmdb_movies)))) == 2


def test_a_newer_snapshot_replaces_what_the_old_one_said(loaded) -> None:
    engine, snapshot = loaded
    load_snapshot(engine, snapshot, progress_every=0)

    newer = snapshot.parent / "newer"
    writer = ShardWriter(newer, shard_size=10)
    writer.write(
        json.dumps(_record(movie_ids=[1, 40], keywords={"keywords": []}, title="Retitled"))
    )
    writer.close()
    load_snapshot(engine, newer, progress_every=0)

    with engine.connect() as connection:
        titles = {row.title for row in connection.execute(select(tmdb_movies))}
        assert titles == {"Retitled"}
        # A keyword the film lost is gone, not left behind by an upsert.
        assert list(connection.execute(select(tmdb_movie_keywords))) == []
        # The keyword dimension itself survives — another film may still use it.
        assert len(list(connection.execute(select(tmdb_keywords)))) == 2
