"""Private Feast + LightGBM sidecar for learned candidate ranking."""

from __future__ import annotations

import logging
import math
import os
import secrets
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

import numpy as np
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import AliasChoices, BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.config import Settings
from src.feature_contract import FEATURE_COLUMNS
from src.features.online import RANKER_FEATURES, create_feature_store
from src.models.artifacts import (
    RANKER_ROUTE_FALLBACK,
    RANKER_ROUTE_LEARNED,
    ServingArtifactBundle,
    ServingManifest,
)
from src.serving.policy import EXCLUSION_FILTER_POLICY, REASON_CHAMPION_MISMATCH
from src.serving.sequence_retrieval import (
    SidecarRetriever,
    serves_from_learned_path,
    sidecar_retriever_for,
    top_up_to_limit,
)

logger = logging.getLogger(__name__)

# Feast names its event-timestamp columns by appending this suffix when
# ``to_dict(include_event_timestamps=True)`` is used.
_FEAST_TIMESTAMP_SUFFIX = "__ts"

# Shape of the one representative rank every worker serves before it is
# reachable. The candidate limit is the widest a request may ask for
# (``RankRequest.candidate_limit`` is capped at 500) rather than the 100 a
# default page asks for, because the expensive part of a cold worker is the
# batched online read and a warm-up that reads a handful of rows leaves the
# wide read cold.
WARMUP_SEED_LIMIT = 8
WARMUP_LIMIT = 10
WARMUP_CANDIDATE_LIMIT = 500
# A user id no tenant issues. Deliberate — see
# ``_assert_online_features_are_materialized`` for why an anonymous warm user is
# what makes the non-degeneracy check mean something.
WARMUP_USER_ID = 0

# ADR 0010 treats these four as a serving invariant, not a test convenience:
# unpinned, the measured p99 was 903.64 ms at 0% host CPU steal; with only these
# changed, 48.99 ms. ``/healthz`` reports them so a deploy check can read back
# what the running container actually got.
_NATIVE_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


class _OnlineResponse(Protocol):
    def to_dict(self, include_event_timestamps: bool = False) -> dict[str, list[Any]]: ...


class OnlineFeatureStore(Protocol):
    def get_online_features(
        self,
        *,
        features: list[str],
        entity_rows: list[dict[str, object]],
    ) -> _OnlineResponse: ...


class ColdStartError(ValueError):
    """Learned retrieval intentionally declines users without history."""


class TenantArtifactMismatchError(ValueError):
    """The request tenant does not match the artifact isolation boundary."""


class ChampionMismatchError(ValueError):
    """The loaded bundle is not the champion the calling tenant is registered on.

    Distinct from ``TenantArtifactMismatchError`` because it is a different
    failure with a different fix. That one is an isolation breach — a tenant
    reaching artifacts that are not theirs — and it is answered 403. This one is
    a version skew inside the right tenant: the registry (``public.tenants``,
    migration 0016) names a champion this sidecar did not load, which happens
    for the length of a rolling deploy and permanently if a promotion updated
    the row without shipping the bundle. Serving anyway would put predictions in
    the audit log under a version that was never promoted, so the request is
    refused and the coordinator degrades to popularity with a reason that says
    which versions disagreed.
    """


class DegenerateWarmupError(RuntimeError):
    """The warm rank completed, but against an online store with nothing in it.

    A ``RuntimeError`` because it is raised inside ``lifespan``: the worker
    never joins the accept loop, the container never reports healthy, and the
    deployment stalls loudly instead of serving every candidate a ranking score
    computed from zeros.
    """


@dataclass(frozen=True)
class WarmupReport:
    """What one worker's warm rank actually did, for the log line and /healthz."""

    warmup_ms: float
    seed_movie_ids: tuple[int, ...]
    candidate_count: int
    ranked_count: int
    seed_count: int
    feature_event_time: float | None


@dataclass(frozen=True)
class RankedItem:
    movie_id: int
    score: float
    features: dict[str, float]
    candidate_source: str
    seed_movie_id: int | None


@dataclass(frozen=True)
class RankingResult:
    items: list[RankedItem]
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    # Time inside the sequence encoder's forward pass, carved out of
    # ``candidate_latency_ms`` rather than added to it. 0.0 for a family that
    # runs no encoder, which is a measurement and not a gap.
    encoder_ms: float
    latency_ms: float
    candidate_sources: dict[str, int]
    # Positive seeds that actually reached a candidate, not the number offered.
    # The caller decides whether it may claim learned retrieval from this.
    seed_count: int
    excluded_count: int
    filter_policy: str
    feature_event_time: float | None
    # Which of the manifest's two ranker routes actually scored this request.
    # Reported rather than inferred from the history length, because the
    # threshold that decides it is a property of the loaded bundle and a reader
    # of an audit row has no way to know which bundle was loaded.
    ranker_route: str
    # The retrieval family that answered, and the checksum of the artifact it
    # answered from. The family is the same string ``candidate_policy`` carries
    # today, and it is reported under its own name because the two are only
    # incidentally equal: ``candidate_policy`` is the coordinator's input to a
    # composite policy label, while this is the audit's answer to "which family
    # ran". The checksum is what makes a row replayable — a version string can be
    # republished over different weights, a SHA-256 cannot.
    retriever_family: str
    retriever_sha256: str


class ModelRankingService:
    """Own loaded artifacts and perform request-time feature lookup only."""

    def __init__(
        self,
        bundle: ServingArtifactBundle,
        feature_store: OnlineFeatureStore,
        *,
        feature_cache_max_entries: int = 256,
    ) -> None:
        self._bundle = bundle
        # Resolved once, at construction, so a rank call never has to ask which
        # family it is serving. A bundle that reached here is fully realised —
        # ``load`` refuses rather than returning a half-loaded one — so this
        # cannot fail for any bundle that booted.
        self._retriever: SidecarRetriever = sidecar_retriever_for(
            retriever=bundle.retriever, candidates=bundle.candidates
        )
        # ``None`` for every bundle published before schema 2, which is what
        # keeps those bundles on the single learned route they have always used.
        self._cold_start_threshold = _declared_cold_start_threshold(bundle.manifest)
        self._feature_store = feature_store
        self._feature_cache_max_entries = feature_cache_max_entries
        self._feature_cache: OrderedDict[
            tuple[str, int, tuple[int, ...]], tuple[pd.DataFrame, float | None]
        ] = OrderedDict()
        self._feature_cache_lock = Lock()

    @property
    def manifest(self) -> ServingManifest:
        return self._bundle.manifest

    def warmup(self) -> WarmupReport:
        """Serve one representative rank so this worker is warm before it serves.

        The previous warm-up predicted a single all-zero feature row. That paid
        LightGBM's lazy native initialization and nothing else: the first real
        request per worker still constructed the Feast client, opened Redis and
        read a full candidate batch, measured at 10.597 s (about 6.567 s of it
        Feast) against the API's 0.5 s client timeout — which the coordinator
        turned into an honest HTTP 200 carrying ``learned=false`` and
        ``fallback_reason="model-server-unavailable"``. Warming *through*
        ``rank`` is the whole point: same retrieval, same batched online read,
        same booster call, so whatever is lazy is paid here rather than by the
        first viewer.

        The input is derived from the loaded bundle rather than from a fixture,
        because a fixture is one more file that can drift away from the
        artifacts it is supposed to prime. No database lookup is involved, so
        this cannot deadlock against release ordering.

        The read is performed before the rank purely so the assertion below can
        inspect the whole candidate matrix; the rank that follows re-uses the
        per-process feature cache rather than opening a second Redis round trip.
        """
        started = time.perf_counter()
        manifest = self._bundle.manifest
        seeds = self._retriever.warmup_seed_movie_ids(self._warmup_seed_count())
        if not seeds:
            raise DegenerateWarmupError(
                "the retrieval stage carries no items to warm from; the serving bundle at "
                "this manifest cannot rank anything"
            )
        # Mirrors what ``rank`` asks the retriever for, so the feature read below
        # is keyed on the exact candidate set the warm rank will look up.
        retrieval = self._retriever.retrieve(
            seeds,
            limit=max(WARMUP_LIMIT, WARMUP_CANDIDATE_LIMIT),
            excluded_movie_ids=(),
            dismissed_movie_ids=(),
        )
        candidate_ids = retrieval.movie_ids
        if not candidate_ids:
            raise DegenerateWarmupError(
                "the candidate index returned nothing for its own lowest item ids; the "
                "serving bundle is not usable for learned retrieval"
            )
        features, feature_event_time = self._online_features(
            tenant_id=manifest.tenant_id,
            user_id=WARMUP_USER_ID,
            candidate_ids=candidate_ids,
        )
        _assert_online_features_are_materialized(features, feature_event_time)
        result = self.rank(
            tenant_id=manifest.tenant_id,
            user_id=WARMUP_USER_ID,
            positive_history_movie_ids=list(seeds),
            excluded_movie_ids=[],
            dismissed_movie_ids=[],
            limit=WARMUP_LIMIT,
            candidate_limit=WARMUP_CANDIDATE_LIMIT,
        )
        return WarmupReport(
            warmup_ms=(time.perf_counter() - started) * 1000,
            seed_movie_ids=tuple(seeds),
            candidate_count=len(candidate_ids),
            ranked_count=len(result.items),
            seed_count=result.seed_count,
            feature_event_time=feature_event_time,
        )

    def _warmup_seed_count(self) -> int:
        """How long the warm-up's synthetic history has to be to warm the real path.

        ``WARMUP_SEED_LIMIT`` was chosen when every bundle answered every request
        from one route. A bundle that declares a cold-start threshold splits that
        in two, and a warm-up shorter than the threshold would warm the *fallback*
        route — paying none of the lazy cost on the path a real warm user takes,
        which is the whole reason the warm-up exists. So it is at least the
        threshold.

        For a sequence bundle this also makes the warm-up the startup detector for
        the fused-attention NaN defect end to end: a threshold-length history is
        shorter than the encoder window, so it is left-padded, so a worker whose
        encoder is unguarded retrieves nothing and dies on ``DegenerateWarmupError``
        instead of serving popularity under a learned model version.
        """
        if self._cold_start_threshold is None:
            return WARMUP_SEED_LIMIT
        return max(WARMUP_SEED_LIMIT, self._cold_start_threshold)

    def rank(
        self,
        *,
        tenant_id: str,
        user_id: int,
        positive_history_movie_ids: list[int],
        excluded_movie_ids: list[int],
        dismissed_movie_ids: list[int],
        limit: int,
        candidate_limit: int,
        champion: ChampionCoordinates | None = None,
    ) -> RankingResult:
        started = time.perf_counter()
        manifest = self._bundle.manifest
        if tenant_id != manifest.tenant_id:
            raise TenantArtifactMismatchError(
                f"tenant {tenant_id!r} cannot use artifacts for {manifest.tenant_id!r}"
            )
        # The tenant check above proves these artifacts belong to this tenant;
        # this one proves they are the *version* the tenant is registered on.
        # Checked before any work is done, because the answer does not depend on
        # the request — a mismatched deployment refuses every rank call, and it
        # should do so without first reading a candidate batch out of Redis.
        if champion is not None and not champion.matches(manifest):
            raise ChampionMismatchError(
                f"tenant {tenant_id!r} is registered on champion {champion.describe()} "
                f"but this sidecar loaded {_describe_manifest(manifest)}"
            )
        if not positive_history_movie_ids:
            raise ColdStartError("learned retrieval requires at least one historical interaction")

        # The route is decided from the history the request carries, before any
        # retrieval, because it selects the booster and a booster swap mid-request
        # would be worse than either choice.
        route = self._route_for(len(set(positive_history_movie_ids)))
        excluded = set(excluded_movie_ids)
        width = max(limit, candidate_limit)
        candidate_started = time.perf_counter()
        retrieval = self._retriever.retrieve(
            positive_history_movie_ids,
            limit=width,
            excluded_movie_ids=excluded,
            dismissed_movie_ids=dismissed_movie_ids,
        )
        # Topping a short retrieval up is the sidecar's job, not a retriever's —
        # see ``top_up_to_limit`` for the argument. For the item-item family this
        # is provably a no-op, because ``CandidateIndex.retrieve`` already filled
        # from the same order before returning.
        topped = top_up_to_limit(
            retrieval,
            limit=width,
            fill_order=self._retriever.fill_order(),
            blocked=excluded | set(dismissed_movie_ids) | set(positive_history_movie_ids),
        )
        retrieval = topped.retrieval
        candidate_ids = retrieval.movie_ids
        candidate_latency_ms = (time.perf_counter() - candidate_started) * 1000
        if not candidate_ids:
            raise ValueError("learned candidate index returned no unseen items")
        if topped.shortfall:
            # Info rather than warning, and deliberately so. A small tenant whose
            # catalog runs out once exclusions are applied hits this on every
            # request, and it is not a fault — nothing is available to return.
            # The neighbouring ``excluded_candidate_blocked`` warning is a
            # contract breach and this is not one. It is still logged, because
            # the other way to arrive here is a retriever quietly under-delivering
            # and a candidate set narrower than the caller asked for should never
            # be invisible.
            logger.info(
                "candidate_shortfall tenant_id=%s user_id=%s route=%s family=%s requested=%s "
                "retrieved=%s filled=%s",
                tenant_id,
                user_id,
                route,
                manifest.retriever.family,
                width,
                len(candidate_ids),
                topped.filled,
            )
        feature_started = time.perf_counter()
        features, feature_event_time = self._online_features(
            tenant_id=tenant_id,
            user_id=user_id,
            candidate_ids=candidate_ids,
        )
        feature_latency_ms = (time.perf_counter() - feature_started) * 1000
        ranker_started = time.perf_counter()
        scores = self._bundle.rankers[route].predict(features)
        order = np.argsort(-scores, kind="stable")
        items: list[RankedItem] = []
        for raw_index in order:
            if len(items) >= limit:
                break
            index = int(raw_index)
            contribution = retrieval.contributions[index]
            # Retrieval already dropped these, so a hit here means the index and
            # the live exclusion set disagreed. Drop it rather than rank it.
            if contribution.movie_id in excluded:
                logger.warning(
                    "excluded_candidate_blocked tenant_id=%s user_id=%s movie_id=%s stage=ranker",
                    tenant_id,
                    user_id,
                    contribution.movie_id,
                )
                continue
            items.append(
                RankedItem(
                    movie_id=contribution.movie_id,
                    score=float(scores[index]),
                    features={
                        column: float(features.iloc[index][column]) for column in FEATURE_COLUMNS
                    },
                    candidate_source=contribution.source,
                    seed_movie_id=contribution.seed_movie_id,
                )
            )
        ranker_latency_ms = (time.perf_counter() - ranker_started) * 1000
        latency_ms = (time.perf_counter() - started) * 1000
        # The version of the booster that actually ran, which is the learned
        # route's only when the learned route ran. A schema 1 bundle points both
        # routes at one artifact, so this is unchanged for every bundle published
        # before the split.
        ranker_version = manifest.route(route).artifact.version
        logger.debug(
            "learned_rank tenant_id=%s user_id=%s candidate_policy=%s "
            "candidate_version=%s ranker_route=%s ranker_version=%s feature_version=%s "
            "candidate_count=%s result_count=%s positive_count=%s seed_count=%s "
            "excluded_count=%s candidate_latency_ms=%.3f encoder_ms=%.3f "
            "feature_latency_ms=%.3f ranker_latency_ms=%.3f latency_ms=%.3f",
            tenant_id,
            user_id,
            manifest.retriever.family,
            manifest.retriever.version,
            route,
            ranker_version,
            manifest.feature_version,
            len(candidate_ids),
            len(items),
            # Offered next to used: a gap between the two is how an index that
            # has never scored this user's titles shows up in the logs.
            len(positive_history_movie_ids),
            retrieval.seed_count,
            retrieval.excluded_count,
            candidate_latency_ms,
            retrieval.encoder_ms,
            feature_latency_ms,
            ranker_latency_ms,
            latency_ms,
        )
        return RankingResult(
            items=items,
            candidate_policy=manifest.retriever.family,
            candidate_version=manifest.retriever.version,
            ranker_version=ranker_version,
            feature_version=manifest.feature_version,
            candidate_latency_ms=candidate_latency_ms,
            feature_latency_ms=feature_latency_ms,
            ranker_latency_ms=ranker_latency_ms,
            encoder_ms=retrieval.encoder_ms,
            latency_ms=latency_ms,
            candidate_sources=retrieval.source_counts(),
            seed_count=retrieval.seed_count,
            excluded_count=retrieval.excluded_count,
            filter_policy=EXCLUSION_FILTER_POLICY,
            feature_event_time=feature_event_time,
            ranker_route=route,
            retriever_family=manifest.retriever.family,
            retriever_sha256=manifest.retriever.primary.sha256,
        )

    def _route_for(self, history_size: int) -> str:
        """Pick the ranking route for a request, from the loaded bundle's threshold.

        The learned route gets the bundle's learned booster and the fallback route
        the incumbent one. Which is which is a property of the *bundle*, not of
        this process: a manifest that declares no ``cold_start_threshold`` — every
        schema 1 bundle, and any schema 2 bundle that chose not to — answers
        everything on the learned route, exactly as this sidecar always has.

        The coordinator applies its own threshold before it calls at all
        (``src/serving/orchestration.py``), so in a matched deployment the
        fallback route fires only for the warm-up and for a bundle whose
        threshold is stricter than the coordinator's. It is checked here anyway
        because the sidecar must not depend on a caller's routing to pick the
        right booster.
        """
        if serves_from_learned_path(
            history_size=history_size, cold_start_threshold=self._cold_start_threshold
        ):
            return RANKER_ROUTE_LEARNED
        return RANKER_ROUTE_FALLBACK

    def _online_features(
        self,
        *,
        tenant_id: str,
        user_id: int,
        candidate_ids: list[int],
    ) -> tuple[pd.DataFrame, float | None]:
        cache_key = (tenant_id, user_id, tuple(candidate_ids))
        # Artifacts and their Feast snapshot are versioned together. A new
        # manifest requires a sidecar restart, which also clears this bounded
        # cache. Candidate ids are part of the key, so immediate rating
        # feedback that changes the unseen set cannot reuse an old matrix.
        with self._feature_cache_lock:
            cached = self._feature_cache.get(cache_key)
            if cached is not None:
                self._feature_cache.move_to_end(cache_key)
                return cached

        response = self._feature_store.get_online_features(
            features=RANKER_FEATURES,
            entity_rows=[
                {"tenant_id": tenant_id, "user_id": user_id, "item_id": item_id}
                for item_id in candidate_ids
            ],
        ).to_dict(include_event_timestamps=True)
        rows: dict[str, list[float]] = {}
        for column in FEATURE_COLUMNS:
            values = response.get(column)
            if values is None or len(values) != len(candidate_ids):
                raise ValueError(f"online feature response is missing candidate-aligned {column!r}")
            rows[column] = [_finite_float(value) for value in values]
        result = (
            pd.DataFrame(rows, columns=FEATURE_COLUMNS),
            _oldest_event_time(response),
        )
        with self._feature_cache_lock:
            self._feature_cache[cache_key] = result
            self._feature_cache.move_to_end(cache_key)
            while len(self._feature_cache) > self._feature_cache_max_entries:
                self._feature_cache.popitem(last=False)
        return result


class ChampionCoordinates(BaseModel):
    """The champion the calling tenant is registered on (``public.tenants``).

    The three coordinates of a ``ServingManifest``, sent by the coordinator on
    every rank request so the sidecar can refuse to answer under a version this
    deployment never promoted.
    """

    candidate_version: str = Field(min_length=1)
    ranker_version: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)

    def matches(self, manifest: ServingManifest) -> bool:
        # ``candidate_version`` is the registry's name for the retrieval stage
        # and stays as it is on the wire; what it is compared against is now the
        # retriever's version, which is the same string a schema 1 bundle put in
        # its candidate ref.
        return (
            self.candidate_version == manifest.retriever.version
            and self.ranker_version == manifest.ranker_version
            and self.feature_version == manifest.feature_version
        )

    def describe(self) -> str:
        return f"{self.candidate_version}/{self.ranker_version}/{self.feature_version}"


class RankRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    user_id: int
    # Positive history and exclusions are separate inputs on purpose: the first
    # seeds retrieval, the second only suppresses. ``history_movie_ids`` stays
    # accepted as an alias so a partially rolled deployment keeps serving.
    positive_history_movie_ids: list[int] = Field(
        validation_alias=AliasChoices("positive_history_movie_ids", "history_movie_ids"),
        serialization_alias="positive_history_movie_ids",
    )
    excluded_movie_ids: list[int] = Field(default_factory=list)
    # Dismissals are the only ids that also remove a seed. They arrive on their
    # own field because ``excluded_movie_ids`` is the caller's whole "never show
    # this" set and therefore contains the watched history — using it to filter
    # seeds is what silently emptied item-item retrieval. Defaulted so an API
    # that predates this field still gets seeded retrieval: the caller's
    # positive history already excludes dismissals at the query.
    dismissed_movie_ids: list[int] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)
    candidate_limit: int = Field(default=100, ge=1, le=500)
    # Optional for the same reason ``dismissed_movie_ids`` is: during a rolling
    # deploy the API and the sidecar are briefly different builds, and an API
    # that predates this field must keep being served rather than have every
    # request refused by the newer sidecar. Absent means "the caller stated no
    # champion", which is checked as far as it can be — the tenant boundary
    # above still holds — and not silently treated as a match.
    champion: ChampionCoordinates | None = None


class RankItemResponse(BaseModel):
    movie_id: int
    score: float
    features: dict[str, float]
    candidate_source: str
    seed_movie_id: int | None


class RankResponse(BaseModel):
    tenant_id: str
    candidate_policy: str
    candidate_version: str
    ranker_version: str
    feature_version: str
    candidate_latency_ms: float
    feature_latency_ms: float
    ranker_latency_ms: float
    latency_ms: float
    candidate_sources: dict[str, int]
    seed_count: int
    excluded_count: int
    filter_policy: str
    feature_event_time: float | None
    # Additive: the coordinator's client reads named keys, so a build that
    # predates these fields keeps parsing the response unchanged.
    ranker_route: str
    # Retrieval provenance for the audit row. Without these, a stored audit
    # cannot say whether item-item or SASRec answered, nor which weights did —
    # which is the question a champion swap makes urgent.
    retriever_family: str
    retriever_sha256: str
    encoder_ms: float
    items: list[RankItemResponse]


_settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manifest_path = _settings.model_artifact_dir / _settings.model_manifest_name
    bundle = ServingArtifactBundle.load(manifest_path)
    service = ModelRankingService(
        bundle,
        create_feature_store(_settings),
        feature_cache_max_entries=_settings.model_feature_cache_max_entries,
    )
    # Warm inside lifespan, before this worker joins uvicorn's accept loop, so
    # every worker is warm by construction rather than by probability. A failure
    # here fails startup: the process exits and the container never reports
    # healthy, which is the outcome we want over a worker that looks ready and
    # whose first real request times out into the popularity fallback.
    report = service.warmup()
    app.state.ranking_service = service
    app.state.warmup = report
    logger.info(
        "Model server warm tenant=%s family=%s candidate=%s ranker=%s features=%s "
        "warmup_ms=%.1f seeds=%s candidates=%s ranked=%s seed_count=%s "
        "feature_event_time=%s workers=%s",
        bundle.manifest.tenant_id,
        bundle.manifest.retriever.family,
        bundle.manifest.retriever.version,
        bundle.manifest.ranker_version,
        bundle.manifest.feature_version,
        report.warmup_ms,
        len(report.seed_movie_ids),
        report.candidate_count,
        report.ranked_count,
        report.seed_count,
        report.feature_event_time,
        _declared_workers(),
    )
    yield


app = FastAPI(
    title="MovieLens Internal Model Server",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
async def healthz(request: Request, response: Response) -> dict[str, Any]:
    """Report this worker's own readiness: 503 until it has served a warm rank.

    Per-worker on purpose — the warm-up, the loaded bundle and the feature cache
    are all per-process, so a shared "the service is up" answer would be a claim
    no single process is in a position to make.

    Under uvicorn a worker only accepts connections once ``lifespan`` has
    returned, so in practice a probe either queues or reaches a warm worker; the
    503 is what makes that guarantee explicit rather than incidental, and it is
    what any other runner of this app object gets.
    """
    report: WarmupReport | None = getattr(request.app.state, "warmup", None)
    service: ModelRankingService | None = getattr(request.app.state, "ranking_service", None)
    payload: dict[str, Any] = {
        "status": "ok" if report is not None else "warming",
        "warm": report is not None,
        "warmup_ms": round(report.warmup_ms, 3) if report is not None else None,
        "workers": _declared_workers(),
        "native_threads": _native_thread_pins(),
    }
    if service is not None:
        payload["tenant_id"] = service.manifest.tenant_id
        payload["candidate_version"] = service.manifest.retriever.version
        payload["ranker_version"] = service.manifest.ranker_version
        payload["feature_version"] = service.manifest.feature_version
    if report is None:
        response.status_code = 503
    return payload


@app.post("/rank", response_model=RankResponse)
async def rank(
    payload: RankRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RankResponse:
    expected = f"Bearer {_settings.model_server_auth_token.get_secret_value()}"
    if authorization is None or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid model-server credentials")
    service: ModelRankingService = request.app.state.ranking_service
    try:
        result = await run_in_threadpool(
            service.rank,
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            positive_history_movie_ids=payload.positive_history_movie_ids,
            excluded_movie_ids=payload.excluded_movie_ids,
            dismissed_movie_ids=payload.dismissed_movie_ids,
            limit=payload.limit,
            candidate_limit=payload.candidate_limit,
            champion=payload.champion,
        )
    except TenantArtifactMismatchError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ChampionMismatchError as exc:
        # 409 rather than 403: the caller is authorized for this tenant, and the
        # conflict is between two versions of the same deployment. The body is a
        # structured code rather than prose because the coordinator classifies
        # on it — a fallback audited as "champion-mismatch" is a promotion that
        # needs finishing, and one audited as "model-server-unavailable" is an
        # outage. Those are different pages at 3am.
        raise HTTPException(
            status_code=409,
            detail={"code": REASON_CHAMPION_MISMATCH, "message": str(exc)},
        ) from exc
    except ColdStartError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RankResponse(
        tenant_id=payload.tenant_id,
        candidate_policy=result.candidate_policy,
        candidate_version=result.candidate_version,
        ranker_version=result.ranker_version,
        feature_version=result.feature_version,
        candidate_latency_ms=result.candidate_latency_ms,
        feature_latency_ms=result.feature_latency_ms,
        ranker_latency_ms=result.ranker_latency_ms,
        latency_ms=result.latency_ms,
        candidate_sources=result.candidate_sources,
        seed_count=result.seed_count,
        excluded_count=result.excluded_count,
        filter_policy=result.filter_policy,
        feature_event_time=result.feature_event_time,
        ranker_route=result.ranker_route,
        retriever_family=result.retriever_family,
        retriever_sha256=result.retriever_sha256,
        encoder_ms=result.encoder_ms,
        items=[
            RankItemResponse(
                movie_id=item.movie_id,
                score=item.score,
                features=item.features,
                candidate_source=item.candidate_source,
                seed_movie_id=item.seed_movie_id,
            )
            for item in result.items
        ],
    )


def _assert_online_features_are_materialized(
    features: pd.DataFrame, feature_event_time: float | None
) -> None:
    """Refuse to report warm when the warm read proves the online store is empty.

    Redis is not a cache here, it is the only online feature store: the feature
    views carry 3650-day TTLs and ``_finite_float`` turns a missing value into
    ``0.0`` rather than an error, so an emptied or evicted store degrades every
    ranking score to a constant with nothing failing anywhere. That silence is
    what this check converts into a boot failure.

    What "non-degenerate" means here follows from the warm user id. The warm
    rank runs as ``WARMUP_USER_ID``, which no tenant issues, so both
    user-scoped views miss by construction: ``user_features`` and
    ``user_item_features`` come back as ``None`` and land in the frame as
    ``0.0``. That is deliberate rather than unfortunate — it leaves
    ``item_features`` as the only view that can put a non-zero number anywhere
    in the matrix, so "at least one non-zero value" is a direct statement about
    whether item rows exist in Redis for the candidates this index retrieves.

    The two halves fail and pass together for the same reason:

    * **Empty or evicted store.** Every column answers ``None`` → every value
      is ``0.0``, and Feast reports second 0 on each ``__ts`` column, which
      ``_oldest_event_time`` discards → no event time. Both halves fail.
    * **Materialized store.** Every candidate this index can produce came from a
      rated title, and the item snapshot is built from the same ratings table,
      so ``item_popularity_all_time >= 1`` and ``item_age_days > 0``, carried by
      a real write timestamp. Both halves pass.

    Keeping the event time in the check is not redundant: it is the only signal
    that the numbers came from rows Feast actually wrote, and a store whose rows
    carry no timestamp would report ``feature_event_time: null`` on every
    prediction audit, which is a freshness claim we would rather not make at
    all. And the check is deliberately not "every value is non-zero" — a zero
    genre affinity for a user that does not exist is the correct answer, not a
    fault.
    """
    if feature_event_time is None:
        raise DegenerateWarmupError(
            "the warm feature read carried no Feast event timestamp, so the online store holds "
            "no rows for this tenant's candidates. Materialize before serving: "
            "python -m src.features.materialize"
        )
    if not bool(np.any(features.to_numpy(dtype=np.float64) != 0.0)):
        raise DegenerateWarmupError(
            f"the warm feature read returned only zeros across {len(features)} candidates, so "
            "every ranking score would be computed from missing features. Materialize before "
            "serving: python -m src.features.materialize"
        )


def _describe_manifest(manifest: ServingManifest) -> str:
    """The loaded bundle in the same shape a champion is written down in."""
    return f"{manifest.retriever.version}/{manifest.ranker_version}/{manifest.feature_version}"


def _declared_cold_start_threshold(manifest: ServingManifest) -> int | None:
    """The history size at or above which this bundle uses its learned route.

    Read off the retriever's declared parameters rather than from
    ``src.evaluation.protocol``, and family-neutrally rather than only for
    sequence bundles. The threshold is a claim the *bundle* makes about which
    users its learned booster was fit for, so a bundle and the sidecar serving it
    can never disagree about it — and a bundle that declares none keeps the
    single-route behaviour every bundle published before schema 2 has.
    """
    threshold = manifest.retriever.params.get("cold_start_threshold")
    if threshold is None:
        return None
    return int(threshold)


def _declared_workers() -> int | None:
    """How many workers the platform says this service runs, or unknown.

    A worker cannot count its siblings, and the warm-up runs once per process,
    so this exists to let whoever reads one worker's ``/healthz`` check the
    declared fan-out against the number of processes they expect to have warmed.
    Unset is reported as unknown rather than guessed: the compose stack passes
    ``--workers`` on the command line, where this process cannot see it.
    """
    raw = os.environ.get("MODEL_SERVER_WORKERS")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _native_thread_pins() -> dict[str, str | None]:
    """Report the ADR 0010 thread pins this process was actually given."""
    return {name: os.environ.get(name) for name in _NATIVE_THREAD_VARIABLES}


def _finite_float(value: Any) -> float:
    result = float(value) if value is not None else 0.0
    return result if math.isfinite(result) else 0.0


def _oldest_event_time(response: dict[str, list[Any]]) -> float | None:
    """Return the oldest Feast event time backing this candidate matrix.

    Freshness is only honest if it reports the *stalest* feature the ranker
    saw, so the audit takes the minimum. Feast appends ``__ts`` columns only
    when the online store carries them; absence is reported as unknown rather
    than as "now".
    """
    oldest: float | None = None
    for column in FEATURE_COLUMNS:
        values = response.get(f"{column}{_FEAST_TIMESTAMP_SUFFIX}")
        if not values:
            continue
        for value in values:
            if value is None:
                continue
            seconds = float(value)
            if not math.isfinite(seconds) or seconds <= 0:
                continue
            if oldest is None or seconds < oldest:
                oldest = seconds
    return oldest
