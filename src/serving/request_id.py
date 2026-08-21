"""Correlation id adoption and echo for every response.

A caller that already has a request id — the Next.js BFF, a load generator, or
an upstream proxy — needs the API to keep using *its* id rather than mint a
second one, otherwise a single user action ends up with two unrelated
identifiers in two logs. This middleware adopts a well-formed inbound
``X-Request-ID`` and echoes it on every response, including the ones that fail
authentication.

Deliberately a raw ASGI middleware rather than ``BaseHTTPMiddleware``: it sits
on the p99-critical recommendation path, and wrapping ``send`` costs a closure
per request instead of an extra task and queue.
"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128
# Set alongside the id so downstream code can tell a caller-supplied value from
# one we minted, without re-parsing the header.
REQUEST_ID_ADOPTED_STATE_KEY = "request_id_adopted"

# Printable ASCII without space, so an adopted value can never smuggle CR/LF
# into a response header or a log line.
_VALID_REQUEST_ID = re.compile(rf"^[\x21-\x7e]{{1,{MAX_REQUEST_ID_LENGTH}}}$")


def resolve_request_id(inbound: str | None) -> tuple[str, bool]:
    """Adopt a safe caller-supplied id, otherwise mint one.

    Returns the id and whether it was adopted. An unusable value is replaced
    rather than rejected with an error: a bad correlation header is the
    caller's problem to notice in its own logs, not a reason to fail a request
    that is otherwise valid.
    """
    if inbound is not None and _VALID_REQUEST_ID.match(inbound):
        return inbound, True
    return str(uuid4()), False


class RequestIdMiddleware:
    """Resolve the request id once and echo it on the way out."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id, adopted = resolve_request_id(Headers(scope=scope).get("x-request-id"))
        # ``scope["state"]`` is what Starlette exposes as ``request.state``, so
        # downstream middleware and handlers read the same resolved value.
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state[REQUEST_ID_ADOPTED_STATE_KEY] = adopted

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
