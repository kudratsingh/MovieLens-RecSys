import { afterEach, describe, expect, it, vi } from "vitest";

import { CATALOG, RECOMMENDATIONS } from "@/lib/resources/definitions";
import {
  fetchResource,
  loadCatalog,
  loadMovieDetail,
  loadOnlineFeatures,
  loadRecommendationAudits,
  loadRecommendations,
  type FetchLike,
} from "@/lib/resources/server";
import { isResourceFailure } from "@/lib/resources/state";

import {
  auditResponse,
  catalogResponse,
  emptyRecommendationResponse,
  movieDetailResponse,
  onlineFeatures,
  recommendationResponse,
} from "./resource-fixtures";

const SESSION = { accessToken: "server-held-token" };

type Recorded = { url: string; init: RequestInit | undefined };

function recorder(response: () => Promise<Response> | Response) {
  const calls: Recorded[] = [];
  const fetchImpl: FetchLike = async (url, init) => {
    calls.push({ url, init });
    return response();
  };
  return { calls, fetchImpl };
}

function json(body: unknown, init: ResponseInit = {}) {
  return () => Response.json(body, init);
}

afterEach(() => vi.unstubAllEnvs());

describe("the server-owned resource client", () => {
  it("sends the session token, a request ID, and no-store to the configured API", async () => {
    vi.stubEnv("RECOMMENDATION_API_URL", "http://api.internal:8000");
    const { calls, fetchImpl } = recorder(json(recommendationResponse));

    const state = await loadRecommendations(900000101, { session: SESSION, limit: 8, fetchImpl });

    expect(state.status).toBe("ready");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(
      "http://api.internal:8000/users/900000101/recommendations?limit=8",
    );
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("authorization")).toBe("Bearer server-held-token");
    expect(headers.get("x-request-id")).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/);
    expect(calls[0].init?.cache).toBe("no-store");
  });

  it("reuses a well-formed caller request ID and ignores a malformed one", async () => {
    const supplied = "9f2c2f30-3f2e-4a1b-9f0f-6f1f7a0c1111";
    const first = recorder(json(recommendationResponse));
    const reused = await loadRecommendations(900000101, {
      session: SESSION,
      requestId: supplied,
      fetchImpl: first.fetchImpl,
    });
    expect(new Headers(first.calls[0].init?.headers).get("x-request-id")).toBe(supplied);
    expect(reused.status === "ready" && reused.requestId).toBe(supplied);

    const second = recorder(json(recommendationResponse));
    await loadRecommendations(900000101, {
      session: SESSION,
      requestId: "bad id\nwith newline",
      fetchImpl: second.fetchImpl,
    });
    expect(new Headers(second.calls[0].init?.headers).get("x-request-id")).not.toBe(
      "bad id\nwith newline",
    );
  });

  it("prefers the audited request ID FastAPI echoes back", async () => {
    const upstreamId = "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001";
    const { fetchImpl } = recorder(
      json(recommendationResponse, { headers: { "X-Request-ID": upstreamId } }),
    );

    const state = await loadRecommendations(900000101, { session: SESSION, fetchImpl });

    expect(state.status === "ready" && state.requestId).toBe(upstreamId);
  });

  it("never forwards a caller-supplied Authorization header", async () => {
    const { calls, fetchImpl } = recorder(json(catalogResponse));

    // The options type exposes no header slot, so the outgoing request can only
    // be composed from the session. This pins that at runtime too.
    await fetchResource(CATALOG, "/users/900000101/catalog", {
      session: SESSION,
      fetchImpl,
    });

    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("authorization")).toBe("Bearer server-held-token");
    expect(headers.get("proxy-authorization")).toBeNull();
  });

  it("applies the resource timeout budget and honours a caller signal instead", async () => {
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout");
    const first = recorder(json(recommendationResponse));
    await loadRecommendations(900000101, { session: SESSION, fetchImpl: first.fetchImpl });
    expect(timeoutSpy).toHaveBeenLastCalledWith(RECOMMENDATIONS.timeoutMs);

    const second = recorder(json(auditResponse));
    await loadRecommendationAudits(900000101, {
      session: SESSION,
      timeoutMs: 9_000,
      fetchImpl: second.fetchImpl,
    });
    expect(timeoutSpy).toHaveBeenLastCalledWith(9_000);

    const controller = new AbortController();
    const third = recorder(json(catalogResponse));
    timeoutSpy.mockClear();
    await loadCatalog(900000101, {
      session: SESSION,
      signal: controller.signal,
      fetchImpl: third.fetchImpl,
    });
    expect(timeoutSpy).not.toHaveBeenCalled();
    expect(third.calls[0].init?.signal).toBe(controller.signal);
  });
});

describe("outcome mapping", () => {
  it("maps an absent or errored session to auth-expired without a network call", async () => {
    const { calls, fetchImpl } = recorder(json(recommendationResponse));

    const missing = await loadRecommendations(900000101, { session: null, fetchImpl });
    const stale = await loadRecommendations(900000101, {
      session: { accessToken: "old", error: "RefreshAccessTokenError" },
      fetchImpl,
    });

    expect(missing.status).toBe("auth-expired");
    expect(stale.status).toBe("auth-expired");
    expect(isResourceFailure(stale) && stale.retryable).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("maps upstream statuses onto resource-local states", async () => {
    const cases = [
      [401, "auth-expired", "session-expired"],
      [403, "forbidden", "forbidden"],
      [404, "not-found", "not-found"],
      [422, "upstream-error", "bad-request"],
      [429, "upstream-error", "rate-limited"],
      [500, "upstream-error", "server"],
      [503, "upstream-error", "server"],
    ] as const;

    for (const [status, expected, reason] of cases) {
      const { fetchImpl } = recorder(json({ detail: "upstream said no" }, { status }));
      const state = await loadRecommendations(900000101, { session: SESSION, fetchImpl });
      expect([status, state.status]).toEqual([status, expected]);
      expect(isResourceFailure(state) && state.reason).toBe(reason);
      expect(isResourceFailure(state) && state.httpStatus).toBe(status);
      expect(isResourceFailure(state) && state.detail).toBe("upstream said no");
    }
  });

  it("maps a timeout and a transport failure to a retryable upstream error", async () => {
    const timedOut: FetchLike = () =>
      Promise.reject(new DOMException("The operation timed out.", "TimeoutError"));
    const offline: FetchLike = () =>
      Promise.reject(Object.assign(new TypeError("fetch failed"), { cause: new Error("ECONNREFUSED") }));

    const timeout = await loadRecommendations(900000101, {
      session: SESSION,
      fetchImpl: timedOut,
    });
    const network = await loadRecommendations(900000101, {
      session: SESSION,
      fetchImpl: offline,
    });

    expect(timeout.status).toBe("upstream-error");
    expect(isResourceFailure(timeout) && timeout.reason).toBe("timeout");
    expect(isResourceFailure(timeout) && timeout.retryable).toBe(true);
    expect(isResourceFailure(network) && network.reason).toBe("network");
  });

  it("treats an unparseable body and a contract violation as upstream errors", async () => {
    const notJson: FetchLike = async () =>
      new Response("<html>502 Bad Gateway</html>", {
        headers: { "content-type": "text/html" },
      });
    const drifted = recorder(json({ ...recommendationResponse, items: [{ movie_id: 1 }] }));

    const invalidJson = await loadRecommendations(900000101, {
      session: SESSION,
      fetchImpl: notJson,
    });
    const invalidPayload = await loadRecommendations(900000101, {
      session: SESSION,
      fetchImpl: drifted.fetchImpl,
    });

    expect(isResourceFailure(invalidJson) && invalidJson.reason).toBe("invalid-json");
    expect(isResourceFailure(invalidPayload) && invalidPayload.reason).toBe("invalid-payload");
    // Neither is worth asking again for: the answer would be the same.
    expect(isResourceFailure(invalidPayload) && invalidPayload.retryable).toBe(false);
  });

  it("separates an empty collection from a failure and keeps the payload", async () => {
    const { fetchImpl } = recorder(json(emptyRecommendationResponse));

    const state = await loadRecommendations(900000101, { session: SESSION, fetchImpl });

    expect(state.status).toBe("empty");
    expect(state.status === "empty" && state.data.policy).toBe("item-item-lightgbm");
    expect(state.status === "empty" && state.source).toBe("live");
  });

  it("refuses an unaddressable persona or movie before opening a connection", async () => {
    const { calls, fetchImpl } = recorder(json(movieDetailResponse));

    const badUser = await loadCatalog(0, { session: SESSION, fetchImpl });
    const badMovie = await loadMovieDetail(900000101, -3, { session: SESSION, fetchImpl });

    expect(badUser.status).toBe("not-found");
    expect(badMovie.status).toBe("not-found");
    expect(calls).toHaveLength(0);
  });

  it("loads technical evidence through the same boundary", async () => {
    const audits = recorder(json(auditResponse));
    const features = recorder(json(onlineFeatures));

    const auditState = await loadRecommendationAudits(900000101, {
      session: SESSION,
      limit: 5,
      fetchImpl: audits.fetchImpl,
    });
    const featureState = await loadOnlineFeatures(900000101, {
      session: SESSION,
      fetchImpl: features.fetchImpl,
    });

    expect(auditState.status).toBe("ready");
    expect(audits.calls[0].url).toContain("/users/900000101/audits?limit=5");
    expect(featureState.status).toBe("ready");
    expect(features.calls[0].url).toContain("/users/900000101/features");
  });
});
