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
            if path == "/healthz":
                assert "security" not in operation
            else:
                assert operation["security"] == [{"BearerAuth": []}]
                assert operation["responses"]["401"]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorResponse"
                }

    assert len(operation_ids) == len(set(operation_ids))
    assert set(operation_ids) == {
        "healthCheck",
        "getCurrentActor",
        "recommendMovies",
        "listRecommendationAudits",
        "listRatingHistory",
        "listDemoPersonas",
        "getOnlineUserFeatures",
        "listDemoCatalog",
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
