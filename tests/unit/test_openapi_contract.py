from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.serving.app import RatingRequest, app


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


def test_feedback_mutations_accept_idempotency_and_revision_contracts() -> None:
    operation = _schema()["paths"]["/users/{user_id}/movies/{movie_id}/rating"]["put"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["Idempotency-Key"]["in"] == "header"
    assert parameters["Idempotency-Key"]["schema"]["anyOf"][0]["format"] == "uuid"
    assert parameters["expected_revision"]["schema"]["anyOf"][0]["minimum"] == 0
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


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
