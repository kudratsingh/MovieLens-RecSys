from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.serving.app import RatingRequest, _movie_details_response, app
from src.serving.catalog import CatalogMovie


def _schema() -> dict[str, Any]:
    app.openapi_schema = None
    return app.openapi()


def test_authenticated_operations_declare_bearer_security_and_stable_ids() -> None:
    schema = _schema()
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Keycloak access token with aud=movielens-api",
    }

    operation_ids: list[str] = []
    for path, path_item in schema["paths"].items():
        for operation in path_item.values():
            if "operationId" not in operation:
                continue
            operation_ids.append(operation["operationId"])
            if path in {"/healthz", "/readyz"}:
                assert "security" not in operation
            else:
                assert operation["security"] == [{"BearerAuth": []}]
                assert operation["responses"]["401"]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorResponse"
                }

    assert len(operation_ids) == len(set(operation_ids))
    assert set(operation_ids) == {
        "healthCheck",
        "readinessCheck",
        "getCurrentActor",
        "recommendMovies",
        "listRecommendationAudits",
        "listRatingHistory",
        "listDemoPersonas",
        "getOnlineUserFeatures",
        "listDemoCatalog",
        "getMovieDetail",
        "setMovieRating",
        "resetDemoRatings",
        "getMovieState",
        "listLibrary",
        "getLiveRatingsTasteSummary",
        "setMovieWatched",
        "removeMovieFromHistory",
        "setMovieStateRating",
        "deleteMovieStateRating",
        "addMovieToWatchlist",
        "removeMovieFromWatchlist",
        "dismissMovie",
        "undoMovieDismissal",
        "getUserPreferences",
        "setUserPreferences",
    }


def test_readiness_contract_documents_both_probe_outcomes() -> None:
    """A deploy gate reads the status code; an operator reads the body. Both
    outcomes are in the contract so neither is folded into a generic error."""
    schema = _schema()
    operation = schema["paths"]["/readyz"]["get"]

    assert set(operation["responses"]) == {"200", "503"}
    for status in ("200", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ReadinessResponse"
        }
    readiness = schema["components"]["schemas"]["ReadinessResponse"]["properties"]
    assert readiness["status"]["enum"] == ["ready", "not-ready"]
    assert readiness["database"]["enum"] == ["ok", "error"]
    assert readiness["model_server"]["enum"] == ["ok", "unavailable"]


def test_rating_schema_exposes_half_star_and_range_constraints() -> None:
    rating = _schema()["components"]["schemas"]["RatingRequest"]["properties"]["rating"]

    assert rating["minimum"] == 0.5
    assert rating["maximum"] == 5.0
    assert rating["multipleOf"] == 0.5


def test_library_contract_bounds_pages_and_exposes_opaque_cursor() -> None:
    operation = _schema()["paths"]["/users/{user_id}/library"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 50
    assert parameters["cursor"]["schema"]["anyOf"][0]["maxLength"] == 1024
    assert operation["operationId"] == "listLibrary"
    assert operation["responses"]["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_library_filters_and_rankings_are_declared_with_their_bounds() -> None:
    """The Seen tab's search, filters and sorts, as the contract a client reads.

    Every bound here is the difference between a refusal the caller can act on
    and an unbounded scan: the enum answers an unknown sort, the year range
    answers a typo, and ``max_length`` answers a pasted essay — all before the
    request reaches a query.
    """
    schema = _schema()
    operation = schema["paths"]["/users/{user_id}/library"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["sort"]["schema"]["enum"] == [
        "recent",
        "title",
        "rating",
        "release",
        "tmdb",
    ]
    assert parameters["q"]["schema"]["anyOf"][0]["maxLength"] == 120
    assert parameters["genre"]["schema"]["anyOf"][0]["maxLength"] == 40
    for bound in ("year_from", "year_to"):
        assert parameters[bound]["schema"]["anyOf"][0]["minimum"] == 1878
        assert parameters[bound]["schema"]["anyOf"][0]["maximum"] == 2100

    response = schema["components"]["schemas"]["LibraryResponse"]
    assert {"genre", "year_from", "year_to"} <= set(response["required"])


def test_a_library_page_reports_an_exact_match_count_and_the_crowd_average() -> None:
    """``matched`` is a count of one viewer's own bounded rows, so it is exact
    and always present — the spotlight's position readout is its only reader
    and the alternative is inventing the number. ``tmdb_rating`` carries the
    average alone; the vote count that qualifies it lives on the detail
    payload, which a list response still does not carry."""
    schemas = _schema()["components"]["schemas"]
    page = schemas["CursorPageResponse"]
    row = schemas["LibraryMovieResponse"]

    assert set(page["required"]) == {"next_cursor", "has_more", "matched"}
    assert page["properties"]["matched"]["type"] == "integer"
    assert "tmdb_rating" in row["required"]
    assert row["properties"]["tmdb_rating"]["anyOf"] == [{"type": "number"}, {"type": "null"}]
    assert "details" not in row["properties"]


def test_feedback_mutations_accept_idempotency_and_revision_contracts() -> None:
    operation = _schema()["paths"]["/users/{user_id}/movies/{movie_id}/rating"]["put"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["Idempotency-Key"]["in"] == "header"
    assert parameters["Idempotency-Key"]["schema"]["anyOf"][0]["format"] == "uuid"
    assert parameters["expected_revision"]["schema"]["anyOf"][0]["minimum"] == 0
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


# Every operation that runs a movie-state mutation, including the two
# compatibility shapes. The list is asserted against the routes themselves in
# `test_serving_error_contract.py`; here it names what the contract must say.
MUTATION_OPERATIONS = {
    ("/users/{user_id}/movies/{movie_id}/watched", "put"),
    ("/users/{user_id}/movies/{movie_id}/watched", "delete"),
    ("/users/{user_id}/movies/{movie_id}/rating", "put"),
    ("/users/{user_id}/movies/{movie_id}/rating", "delete"),
    ("/users/{user_id}/movies/{movie_id}/watchlist", "put"),
    ("/users/{user_id}/movies/{movie_id}/watchlist", "delete"),
    ("/users/{user_id}/movies/{movie_id}/dismissal", "put"),
    ("/users/{user_id}/movies/{movie_id}/dismissal", "delete"),
    ("/users/{user_id}/ratings/{movie_id}", "put"),
    ("/users/{user_id}/ratings", "delete"),
}


def test_an_error_body_can_name_itself_without_promising_to() -> None:
    """``code`` is optional rather than required-and-nullable, deliberately.

    Most 4xx on this surface carry a status that says everything there is to
    say and send no code at all, so a required field would publish a `null` the
    service never emits and make every client handle it.
    """
    error = _schema()["components"]["schemas"]["ErrorResponse"]

    assert error["required"] == ["detail"]
    assert error["properties"]["code"]["type"] == "string"
    assert error["properties"]["detail"]["type"] == "string"


@pytest.mark.parametrize(("path", "method"), sorted(MUTATION_OPERATIONS))
def test_a_mutation_documents_both_bodies_its_422_can_carry(path: str, method: str) -> None:
    """A refusal and a validation error share the status, so both are declared.

    Declaring `422` at all suppresses the entry FastAPI generates for its own
    validation error, and dropping that would leave a client parsing a shape
    the contract never mentions (issue #74). The two are told apart by `code`.
    """
    operation = _schema()["paths"][path][method]
    schema = operation["responses"]["422"]["content"]["application/json"]["schema"]

    assert schema["anyOf"] == [
        {"$ref": "#/components/schemas/ErrorResponse"},
        {"$ref": "#/components/schemas/HTTPValidationError"},
    ]
    assert "transition_refused" in operation["responses"]["422"]["description"]


@pytest.mark.parametrize(("path", "method"), sorted(MUTATION_OPERATIONS))
def test_a_mutation_409_names_the_two_races_it_still_covers(path: str, method: str) -> None:
    operation = _schema()["paths"][path][method]
    documented = operation["responses"]["409"]

    assert documented["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert "revision_conflict" in documented["description"]
    assert "idempotency_conflict" in documented["description"]
    # The transition refusal moved off this status and must not be advertised
    # here any more — that wording is what the old client parsed by sentence.
    assert "transition" not in documented["description"]


def test_both_bodies_a_mutation_422_references_are_defined() -> None:
    """The `anyOf` above points at two components; a dangling `$ref` is a lie.

    `HTTPValidationError` in particular is generated only because some other
    operation still leaves FastAPI to document its own 422.
    """
    schemas = _schema()["components"]["schemas"]

    assert "ErrorResponse" in schemas
    assert "HTTPValidationError" in schemas
    assert "ValidationError" in schemas


def test_catalog_contract_is_bounded_and_exposes_opaque_page_state() -> None:
    schema = _schema()
    operation = schema["paths"]["/users/{user_id}/catalog"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["limit"]["schema"] == {
        "default": 24,
        "maximum": 48,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    assert parameters["sort"]["schema"]["enum"] == ["title", "newest", "popular"]
    assert operation["responses"]["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    page = schema["components"]["schemas"]["CatalogPageInfo"]
    assert set(page["required"]) == {"next_cursor", "has_more"}
    item = schema["components"]["schemas"]["CatalogItem"]
    assert "state" in item["required"]
    assert "rating" not in item["properties"]


def test_recommendation_items_publish_the_callers_own_movie_state() -> None:
    """Without this a client has no revision to write against and its first
    press is rejected as stale. Required-and-nullable, exactly as CatalogItem
    declares the same overlay, so ``state: null`` means "no row" rather than
    "this response did not look"."""
    schemas = _schema()["components"]["schemas"]
    item = schemas["RecommendationItem"]

    assert "state" in item["required"]
    assert item["properties"]["state"]["anyOf"] == [
        {"$ref": "#/components/schemas/MovieStateResponse"},
        {"type": "null"},
    ]
    assert item["properties"]["state"] == schemas["CatalogItem"]["properties"]["state"]


def test_library_and_history_rows_publish_local_artwork() -> None:
    schemas = _schema()["components"]["schemas"]

    for name in ("LibraryMovieResponse", "HistoryItem"):
        properties = schemas[name]["properties"]
        assert {"release_year", "poster_url"} <= set(schemas[name]["required"])
        assert properties["release_year"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
        assert properties["poster_url"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_detail_carries_the_tmdb_payload_and_the_list_deliberately_does_not() -> None:
    """Detail is a superset of a catalog item, declared as its own schema.

    The split is the contract: a Browse page of up to 48 titles must not grow a
    backdrop, six cast members and a trailer per row to render a poster grid
    (``docs/frontend/catalog-contract.md``).
    """
    schemas = _schema()["components"]["schemas"]

    assert schemas["MovieDetailResponse"]["properties"]["item"] == {
        "$ref": "#/components/schemas/MovieDetailItem"
    }
    assert "details" not in schemas["CatalogItem"]["properties"]
    assert schemas["CatalogResponse"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/CatalogItem"
    }
    detail = schemas["MovieDetailItem"]
    assert "details" in detail["required"], "null means 'no payload', not 'not looked up'"
    assert detail["properties"]["details"]["anyOf"] == [
        {"$ref": "#/components/schemas/MovieDetails"},
        {"type": "null"},
    ]
    # Every field a catalog item carries is still on the detail item.
    assert set(schemas["CatalogItem"]["properties"]) < set(detail["properties"])

    details = schemas["MovieDetails"]
    assert set(details["required"]) == {
        "tagline",
        "runtime_minutes",
        "release_date",
        "backdrop_url",
        "tmdb_rating",
        "directors",
        "cast",
        "trailer",
        "fetched_at",
    }
    assert details["properties"]["cast"]["items"] == {
        "$ref": "#/components/schemas/MovieCastMember"
    }
    assert details["properties"]["trailer"]["anyOf"] == [
        {"$ref": "#/components/schemas/MovieTrailer"},
        {"type": "null"},
    ]
    assert schemas["MovieTrailer"]["properties"]["provider"]["const"] == "youtube"
    assert set(schemas["TmdbRating"]["required"]) == {"average", "count"}
    assert set(schemas["MovieCastMember"]["required"]) == {"name", "character", "profile_url"}


def _catalog_movie(details: object) -> CatalogMovie:
    return CatalogMovie(
        movie_id=1,
        title="Alpha (1990)",
        genres=["Drama"],
        tmdb_id="101",
        release_year=1990,
        poster_url=None,
        overview=None,
        metadata_source="reviewed-fixture",
        source_status="complete",
        state=None,
        interaction_count=0,
        details=details,  # type: ignore[arg-type]
    )


def test_a_payload_that_does_not_validate_degrades_to_null(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A malformed row must not take the detail page down with it.

    It is the module that was added last; the poster, the synopsis and the
    rating panel do not depend on it, so the response drops the field and says
    so in the log rather than answering 500.
    """
    valid = {
        "tagline": None,
        "runtime_minutes": 101,
        "release_date": "1990-04-01",
        "backdrop_url": None,
        "tmdb_rating": {"average": 7.4, "count": 12},
        "directors": [],
        "cast": [{"name": "A Star", "character": None, "profile_url": None}],
        "trailer": {"provider": "youtube", "key": "abc123", "name": "Trailer"},
        "fetched_at": "2026-08-28T00:00:00+00:00",
    }

    assert _movie_details_response(_catalog_movie(None)) is None
    parsed = _movie_details_response(_catalog_movie(valid))
    assert parsed is not None
    assert parsed.trailer is not None and parsed.trailer.key == "abc123"
    assert parsed.tmdb_rating is not None and parsed.tmdb_rating.count == 12

    with caplog.at_level("WARNING"):
        assert _movie_details_response(_catalog_movie({"tagline": "only this"})) is None
        # A provider this API does not serve is refused rather than passed on
        # to a client that would have to guess how to embed it.
        assert (
            _movie_details_response(
                _catalog_movie(dict(valid, trailer={"provider": "vimeo", "key": "k", "name": "n"}))
            )
            is None
        )
    assert "catalog detail payload failed validation" in caplog.text


@pytest.mark.parametrize("rating", [0.5, 1.0, 4.5, 5.0])
def test_rating_request_accepts_half_star_values(rating: float) -> None:
    assert RatingRequest(rating=rating).rating == rating


@pytest.mark.parametrize(
    "rating",
    [0.0, 0.25, 5.5, float("nan"), float("inf"), float("-inf")],
)
def test_rating_request_rejects_out_of_contract_values(rating: float) -> None:
    with pytest.raises(ValidationError):
        RatingRequest(rating=rating)
