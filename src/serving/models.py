"""Strict slim-client contract for the private learned-model sidecar."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.feature_contract import FEATURE_COLUMNS
from src.serving.policy import (
    ARTIFACT_SHA256_NOT_PINNED,
    FILTER_POLICY_NOT_RUN,
    RANKER_ROUTE_LEARNED,
    RANKER_ROUTES,
    REASON_CHAMPION_MISMATCH,
)
from src.serving.tenancy import TenantChampion

# A sidecar that predates the split inputs still answers, but it cannot
# attribute a candidate. Say so rather than inventing a source in the audit.
CANDIDATE_SOURCE_UNKNOWN = "unknown"

# SHA-256 as this project writes it everywhere: lowercase hex, no prefix.
_SHA256_HEX_LENGTH = 64


class ModelServerContractError(ValueError):
    """The sidecar response is unsafe or incompatible with this API."""


class ModelServerChampionMismatchError(Exception):
    """The sidecar refused: its bundle is not this tenant's registered champion.

    Deliberately not a ``ModelServerContractError``. That name means "the
    sidecar answered something unsafe" and the coordinator audits it as an
    outage; this is a healthy sidecar giving a correct answer to a question
    about versions, and it needs its own reason in the audit or a half-finished
    promotion looks exactly like a broken one.
    """


@dataclass(frozen=True)
class RankedModelItem:
    movie_id: int
    score: float
    features: dict[str, float]
    candidate_source: str = CANDIDATE_SOURCE_UNKNOWN
    seed_movie_id: int | None = None


@dataclass(frozen=True)
class ModelRankingResult:
    tenant_id: str
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    latency_ms: float
    items: list[RankedModelItem]
    candidate_sources: dict[str, int] = field(default_factory=dict)
    # Seeds the sidecar actually retrieved from. Zero means the first stage
    # contributed nothing, whatever the manifest calls the candidate policy.
    seed_count: int = 0
    excluded_count: int = 0
    filter_policy: str = FILTER_POLICY_NOT_RUN
    feature_event_time: float | None = None
    # Retrieval provenance. The defaults are what a sidecar that predates these
    # fields means, not placeholders: it published one ranker and answered every
    # request from it (``ModelRankingService._route_for`` with no declared
    # threshold), it pins no checksum, and it runs no encoder.
    ranker_route: str = RANKER_ROUTE_LEARNED
    retriever_family: str = ""
    retriever_sha256: str = ARTIFACT_SHA256_NOT_PINNED
    encoder_ms: float = 0.0

    def __post_init__(self) -> None:
        # A sidecar that omits ``retriever_family`` still told us which family it
        # is — it has been sending that string as ``candidate_policy`` since
        # before the field existed. Resolving it on the dataclass rather than in
        # the parser means every construction path agrees, so a result assembled
        # by hand cannot put an empty family into an audit row.
        if not self.retriever_family:
            object.__setattr__(self, "retriever_family", self.candidate_policy)


class ModelServerClient:
    def __init__(
        self,
        *,
        base_url: str,
        auth_token: str,
        timeout_seconds: float = 0.5,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {auth_token}"},
        )

    async def rank(
        self,
        *,
        tenant_id: str,
        user_id: int,
        positive_history_movie_ids: list[int],
        excluded_movie_ids: list[int] | None = None,
        dismissed_movie_ids: list[int] | None = None,
        limit: int,
        candidate_limit: int = 100,
        champion: TenantChampion | None = None,
    ) -> ModelRankingResult:
        excluded = list(excluded_movie_ids or ())
        dismissed = list(dismissed_movie_ids or ())
        payload: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "positive_history_movie_ids": positive_history_movie_ids,
            "excluded_movie_ids": excluded,
            # Only dismissals may narrow the seed set. ``excluded`` carries
            # the user's own watched titles, so the sidecar must never take
            # it as a reason to stop seeding retrieval.
            "dismissed_movie_ids": dismissed,
            "limit": limit,
            "candidate_limit": candidate_limit,
        }
        if champion is not None:
            # Omitted rather than sent as null when the tenant has no champion,
            # so an older sidecar and a newer one read the same request the same
            # way. In practice the coordinator does not call at all in that case.
            payload["champion"] = {
                "candidate_version": champion.candidate_version,
                "ranker_version": champion.ranker_version,
                "feature_version": champion.feature_version,
            }
        response = await self._client.post("/rank", json=payload)
        _raise_for_champion_mismatch(response)
        response.raise_for_status()
        try:
            return _parse_result(
                response.json(),
                expected_tenant=tenant_id,
                # A returned id that the caller asked to suppress is a contract
                # breach, not something to quietly drop: the caller falls back
                # to a policy it can audit instead of shipping the leak.
                seen_movie_ids=set(positive_history_movie_ids) | set(excluded),
                limit=limit,
            )
        except ModelServerContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelServerContractError("model-server response has an invalid shape") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


def _raise_for_champion_mismatch(response: httpx.Response) -> None:
    """Turn the sidecar's coded 409 into its own exception, before the generic one.

    The sidecar has two 409s — a cold-start decline and this — so the status
    alone is not the signal; the ``code`` in the body is. Anything that does not
    parse as that exact code is left to ``raise_for_status`` rather than guessed
    at, because a fallback reason invented from an unreadable body is worse than
    the honest "the sidecar failed" the coordinator already has.
    """
    if response.status_code != 409:
        return
    try:
        detail = response.json().get("detail")
    except ValueError:
        return
    if isinstance(detail, dict) and detail.get("code") == REASON_CHAMPION_MISMATCH:
        message = detail.get("message")
        raise ModelServerChampionMismatchError(
            str(message) if message else "the sidecar is not this tenant's champion"
        )


def _parse_result(
    value: Any,
    *,
    expected_tenant: str,
    seen_movie_ids: set[int],
    limit: int,
) -> ModelRankingResult:
    if not isinstance(value, dict):
        raise ModelServerContractError("model-server response must be an object")
    tenant_id = _nonempty_string(value, "tenant_id")
    if tenant_id != expected_tenant:
        raise ModelServerContractError(
            f"model-server returned tenant {tenant_id!r} for {expected_tenant!r} request"
        )
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > limit:
        raise ModelServerContractError("model-server items must be a limit-bounded list")
    items: list[RankedModelItem] = []
    returned_ids: set[int] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ModelServerContractError("model-server item must be an object")
        try:
            movie_id = int(raw["movie_id"])
            score = float(raw["score"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelServerContractError("model-server item fields are invalid") from exc
        if movie_id <= 0:
            raise ModelServerContractError(f"model-server returned invalid movie {movie_id}")
        if movie_id in seen_movie_ids:
            raise ModelServerContractError(f"model-server returned seen movie {movie_id}")
        if movie_id in returned_ids:
            raise ModelServerContractError(f"model-server returned duplicate movie {movie_id}")
        if not math.isfinite(score):
            raise ModelServerContractError(f"model-server returned non-finite score for {movie_id}")
        raw_features = raw.get("features")
        if not isinstance(raw_features, dict) or set(raw_features) != set(FEATURE_COLUMNS):
            raise ModelServerContractError(
                f"model-server returned incompatible features for movie {movie_id}"
            )
        features: dict[str, float] = {}
        for column in FEATURE_COLUMNS:
            try:
                feature_value = float(raw_features[column])
            except (TypeError, ValueError) as exc:
                raise ModelServerContractError(
                    f"model-server returned invalid {column!r} for movie {movie_id}"
                ) from exc
            if not math.isfinite(feature_value):
                raise ModelServerContractError(
                    f"model-server returned non-finite {column!r} for movie {movie_id}"
                )
            features[column] = feature_value
        returned_ids.add(movie_id)
        items.append(
            RankedModelItem(
                movie_id=movie_id,
                score=score,
                features=features,
                candidate_source=_optional_source(raw, movie_id),
                seed_movie_id=_optional_seed(raw, movie_id),
            )
        )
    return ModelRankingResult(
        tenant_id=tenant_id,
        candidate_policy=_nonempty_string(value, "candidate_policy"),
        candidate_version=_nonempty_string(value, "candidate_version"),
        ranker_version=_nonempty_string(value, "ranker_version"),
        feature_version=_nonempty_string(value, "feature_version"),
        candidate_latency_ms=_nonnegative_float(value, "candidate_latency_ms"),
        feature_latency_ms=_nonnegative_float(value, "feature_latency_ms"),
        ranker_latency_ms=_nonnegative_float(value, "ranker_latency_ms"),
        latency_ms=_nonnegative_float(value, "latency_ms"),
        items=items,
        candidate_sources=_source_counts(value),
        seed_count=_optional_count(value, "seed_count"),
        excluded_count=_optional_count(value, "excluded_count"),
        filter_policy=_optional_filter_policy(value),
        feature_event_time=_optional_event_time(value),
        ranker_route=_optional_ranker_route(value),
        retriever_family=_optional_retriever_family(value),
        retriever_sha256=_optional_sha256(value),
        encoder_ms=_optional_encoder_ms(value),
    )


def _nonempty_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ModelServerContractError(f"model-server response has no valid {key!r}")
    return result


def _nonnegative_float(value: dict[str, Any], key: str) -> float:
    try:
        result = float(value[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ModelServerContractError(f"model-server response has no valid {key!r}") from exc
    if not math.isfinite(result) or result < 0:
        raise ModelServerContractError(f"model-server returned invalid {key!r}")
    return result


# The attribution block below is provenance for the audit, not a safety
# control. It is validated when present and defaulted when absent so a sidecar
# rollout skew degrades the explanation instead of the recommendation.
def _optional_source(raw: dict[str, Any], movie_id: int) -> str:
    source = raw.get("candidate_source")
    if source is None:
        return CANDIDATE_SOURCE_UNKNOWN
    if not isinstance(source, str) or not source:
        raise ModelServerContractError(
            f"model-server returned an invalid candidate source for movie {movie_id}"
        )
    return source


def _optional_seed(raw: dict[str, Any], movie_id: int) -> int | None:
    seed = raw.get("seed_movie_id")
    if seed is None:
        return None
    try:
        resolved = int(seed)
    except (TypeError, ValueError) as exc:
        raise ModelServerContractError(
            f"model-server returned an invalid seed for movie {movie_id}"
        ) from exc
    if resolved <= 0:
        raise ModelServerContractError(
            f"model-server returned an invalid seed for movie {movie_id}"
        )
    return resolved


def _source_counts(value: dict[str, Any]) -> dict[str, int]:
    raw = value.get("candidate_sources")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ModelServerContractError("model-server candidate_sources must be an object")
    counts: dict[str, int] = {}
    for name, count in raw.items():
        try:
            resolved = int(count)
        except (TypeError, ValueError) as exc:
            raise ModelServerContractError(
                f"model-server returned an invalid candidate source count for {name!r}"
            ) from exc
        if not isinstance(name, str) or not name or resolved < 0:
            raise ModelServerContractError(
                f"model-server returned an invalid candidate source count for {name!r}"
            )
        counts[name] = resolved
    return counts


def _optional_count(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    if raw is None:
        return 0
    try:
        resolved = int(raw)
    except (TypeError, ValueError) as exc:
        raise ModelServerContractError(f"model-server returned an invalid {key!r}") from exc
    if resolved < 0:
        raise ModelServerContractError(f"model-server returned an invalid {key!r}")
    return resolved


def _optional_filter_policy(value: dict[str, Any]) -> str:
    raw = value.get("filter_policy")
    if raw is None:
        return FILTER_POLICY_NOT_RUN
    if not isinstance(raw, str) or not raw:
        raise ModelServerContractError("model-server returned an invalid 'filter_policy'")
    return raw


def _optional_ranker_route(value: dict[str, Any]) -> str:
    """Which booster scored the request, held to the two routes a bundle declares.

    Validated rather than passed through, because this is the field an audit
    query groups on to answer "which requests did not get the learned ranker".
    A free-text route would make that query quietly incomplete, and an unreadable
    one is a sidecar contract breach the coordinator should audit as an outage
    rather than absorb.
    """
    raw = value.get("ranker_route")
    if raw is None:
        return RANKER_ROUTE_LEARNED
    if raw not in RANKER_ROUTES:
        raise ModelServerContractError(f"model-server returned an unknown ranker route {raw!r}")
    return str(raw)


def _optional_retriever_family(value: dict[str, Any]) -> str:
    """The family name, or empty so the result falls back to ``candidate_policy``."""
    raw = value.get("retriever_family")
    if raw is None:
        return ""
    if not isinstance(raw, str) or not raw:
        raise ModelServerContractError("model-server returned an invalid 'retriever_family'")
    return raw


def _optional_sha256(value: dict[str, Any]) -> str:
    """The retriever artifact's checksum, or the honest empty string.

    Shape-checked but not recomputed: the API image holds no artifacts, so the
    only thing it can establish is that the sidecar sent something that could be
    a digest. Anything else would put a value into a replay column that no
    artifact ever hashed to.
    """
    raw = value.get("retriever_sha256")
    if raw is None:
        return ARTIFACT_SHA256_NOT_PINNED
    if not isinstance(raw, str) or (raw and len(raw) != _SHA256_HEX_LENGTH):
        raise ModelServerContractError("model-server returned an invalid 'retriever_sha256'")
    return raw


def _optional_encoder_ms(value: dict[str, Any]) -> float:
    raw = value.get("encoder_ms")
    if raw is None:
        return 0.0
    try:
        resolved = float(raw)
    except (TypeError, ValueError) as exc:
        raise ModelServerContractError("model-server returned an invalid 'encoder_ms'") from exc
    if not math.isfinite(resolved) or resolved < 0:
        raise ModelServerContractError("model-server returned an invalid 'encoder_ms'")
    return resolved


def _optional_event_time(value: dict[str, Any]) -> float | None:
    raw = value.get("feature_event_time")
    if raw is None:
        return None
    try:
        resolved = float(raw)
    except (TypeError, ValueError) as exc:
        raise ModelServerContractError(
            "model-server returned an invalid 'feature_event_time'"
        ) from exc
    if not math.isfinite(resolved) or resolved < 0:
        raise ModelServerContractError("model-server returned an invalid 'feature_event_time'")
    return resolved
