from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.serving.request_id import (
    MAX_REQUEST_ID_LENGTH,
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    resolve_request_id,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/users/{user_id}/catalog")
    async def catalog(user_id: int, request: Request) -> dict[str, str]:
        return {"seen_request_id": request.state.request_id}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise ValueError("handler failed")

    return app


def test_a_well_formed_inbound_request_id_is_adopted() -> None:
    client = TestClient(_app())

    response = client.get("/users/7/catalog", headers={"X-Request-ID": "bff-01HZX9Q2K4-discover"})

    assert response.headers[REQUEST_ID_HEADER] == "bff-01HZX9Q2K4-discover"
    assert response.json()["seen_request_id"] == "bff-01HZX9Q2K4-discover"


@pytest.mark.parametrize(
    "inbound",
    [
        "",
        " ",
        "has space",
        "line\nbreak",
        "carriage\rreturn",
        "tab\tseparated",
        "x" * (MAX_REQUEST_ID_LENGTH + 1),
    ],
)
def test_an_unusable_inbound_request_id_is_replaced_with_a_minted_one(inbound: str) -> None:
    client = TestClient(_app())

    response = client.get("/users/7/catalog", headers={"X-Request-ID": inbound})

    echoed = response.headers[REQUEST_ID_HEADER]
    assert echoed != inbound
    # A rejected header must not fail the request, and the replacement has to
    # be something we can correlate on, so it is a UUID.
    assert response.status_code == 200
    assert UUID(echoed)


def test_a_request_without_the_header_gets_a_minted_id() -> None:
    client = TestClient(_app())

    response = client.get("/users/7/catalog")

    assert UUID(response.headers[REQUEST_ID_HEADER])
    assert response.json()["seen_request_id"] == response.headers[REQUEST_ID_HEADER]


def test_every_route_echoes_the_header_not_just_audited_ones() -> None:
    client = TestClient(_app())

    catalog = client.get("/users/7/catalog", headers={"X-Request-ID": "abc-123"})
    missing = client.get("/nope", headers={"X-Request-ID": "abc-123"})

    assert catalog.headers[REQUEST_ID_HEADER] == "abc-123"
    assert missing.status_code == 404
    assert missing.headers[REQUEST_ID_HEADER] == "abc-123"


def test_two_requests_receive_distinct_minted_ids() -> None:
    client = TestClient(_app())

    first = client.get("/users/7/catalog")
    second = client.get("/users/7/catalog")

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


def test_resolver_rejects_non_ascii_before_it_can_reach_a_header() -> None:
    # Not exercised through the client: httpx will not even transmit a header
    # it cannot encode, so the resolver is the layer that has to hold.
    assert resolve_request_id("unicode-é")[1] is False
    assert resolve_request_id("\x7f")[1] is False


def test_resolver_accepts_the_maximum_length_and_rejects_one_more() -> None:
    at_limit = "a" * MAX_REQUEST_ID_LENGTH
    over_limit = "a" * (MAX_REQUEST_ID_LENGTH + 1)

    assert resolve_request_id(at_limit) == (at_limit, True)
    assert resolve_request_id(over_limit)[1] is False
    assert resolve_request_id(None)[0] != resolve_request_id(None)[0]
