import { describe, expect, it, vi } from "vitest";

import {
  isTransitionRefusal,
  mutateMovieState,
  newIdempotencyKey,
  movieStatePath,
} from "@/lib/movie-state/mutate";
import { movieState } from "./resource-fixtures";

const COMMITTED = {
  request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
  replayed: false,
  outcome: "changed" as const,
  state: { ...movieState, revision: 7, watchlisted_at: "2026-08-21T09:00:00Z" },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answers the CSRF probe first, then the mutation itself. */
function stubFetch(mutation: Response | Error) {
  return vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/auth/csrf")) {
      return jsonResponse({ csrfToken: "csrf-token-value" });
    }
    if (mutation instanceof Error) throw mutation;
    return mutation;
  }) as unknown as typeof fetch;
}

const baseInput = {
  userId: 900000101,
  movieId: 101,
  resource: "watchlist" as const,
  method: "PUT" as const,
  expectedRevision: 6,
};

describe("committing a movie-state mutation", () => {
  it("sends the revision it rendered, a fresh idempotency key, and the CSRF token", async () => {
    const fetchImpl = stubFetch(jsonResponse(COMMITTED));
    await mutateMovieState({ ...baseInput, fetchImpl });

    const call = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[1];
    const [url, init] = call as [string, RequestInit];
    expect(url).toBe("/api/users/900000101/movies/101/watchlist?expected_revision=6");
    expect(init.method).toBe("PUT");
    expect(init.credentials).toBe("same-origin");

    const headers = new Headers(init.headers);
    expect(headers.get("x-csrf-token")).toBe("csrf-token-value");
    expect(headers.get("Idempotency-Key")).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(headers.get("X-Request-ID")).toBeTruthy();
    // No body and no content type for a resource that carries neither.
    expect(init.body).toBeUndefined();
  });

  it("returns the committed state rather than the value it hoped for", async () => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse(COMMITTED)),
    });

    expect(result).toMatchObject({
      status: "committed",
      outcome: "changed",
      replayed: false,
    });
    expect(result.status === "committed" && result.state.revision).toBe(7);
  });

  it("sends a rating body only for a rating write", async () => {
    const fetchImpl = stubFetch(jsonResponse(COMMITTED));
    await mutateMovieState({
      ...baseInput,
      resource: "rating",
      rating: 4,
      fetchImpl,
    });

    const [, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock
      .calls[1] as [string, RequestInit];
    expect(init.body).toBe(JSON.stringify({ rating: 4 }));
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("reports a revision conflict as its own outcome, not a generic failure", async () => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(
        jsonResponse({ detail: "state revision does not match" }, 409),
      ),
    });

    expect(result).toMatchObject({
      status: "conflict",
      detail: "state revision does not match",
    });
  });

  it("maps an expired session to the auth-expired state", async () => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail: "expired" }, 401)),
    });

    expect(result.status).toBe("failed");
    expect(result.status === "failed" && result.failure.status).toBe("auth-expired");
  });

  it("refuses a response that does not match the mutation contract", async () => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ state: { revision: "seven" } })),
    });

    expect(result.status).toBe("failed");
    expect(result.status === "failed" && result.failure.reason).toBe("invalid-payload");
  });

  it("maps an unreachable API to a retryable transport failure", async () => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(new TypeError("fetch failed")),
    });

    expect(result.status).toBe("failed");
    expect(result.status === "failed" && result.failure.retryable).toBe(true);
  });

  it("fails closed when no CSRF token can be obtained", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ detail: "no" }, 500),
    ) as unknown as typeof fetch;

    const result = await mutateMovieState({ ...baseInput, fetchImpl });
    expect(result.status).toBe("failed");
    // The mutation itself was never attempted.
    expect((fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(1);
  });
});

/**
 * The API documents one `409` for three conditions, so the client tells them
 * apart by body. Every string below is quoted verbatim from the error it is
 * raised with in `src/serving/feedback.py`; if one of them is reworded there
 * without this list moving, this is the test that says so.
 */
describe("telling a transition refusal apart from a concurrency conflict", () => {
  // `StateRevisionConflictError` and `IdempotencyConflictError`.
  const CONCURRENCY = [
    "state revision 3 is stale; current revision is 5",
    "idempotency key was already used for another mutation",
    "idempotency key was already used with a different rating",
  ];

  // `InvalidStateTransitionError`, the three product rules it states.
  const REFUSALS = [
    "a watched movie cannot be added to the watchlist",
    "undo dismissal before adding this movie to watchlist",
    "rating_set requires a rating",
  ];

  it.each(CONCURRENCY)("treats %j as a conflict the write path can recover", async (detail) => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail }, 409)),
    });

    expect(result).toMatchObject({ status: "conflict", detail });
    expect(isTransitionRefusal(detail)).toBe(false);
  });

  it.each(REFUSALS)("treats %j as a refusal that carries its own sentence", async (detail) => {
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail }, 409)),
    });

    expect(result).toMatchObject({ status: "refused", detail });
    expect(isTransitionRefusal(detail)).toBe(true);
  });

  it("keeps a 409 with nothing readable in it a conflict", async () => {
    // With no sentence to show, the generic recovery is all there is.
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(new Response(null, { status: 409 })),
    });

    expect(result).toMatchObject({ status: "conflict", detail: null });
    expect(isTransitionRefusal(null)).toBe(false);
    expect(isTransitionRefusal("   ")).toBe(false);
  });

  it("reads an unrecognised 409 as a refusal rather than inventing a race", async () => {
    // The safer direction to be wrong in: an unknown rule shown in the API's
    // own words is honest, while an unknown rule shown as "changed somewhere
    // else" is a claim about state nobody touched.
    const detail = "this persona cannot be written to during a replay window";
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail }, 409)),
    });

    expect(result).toMatchObject({ status: "refused", detail });
  });
});

describe("mutation request shape", () => {
  it("never sends a negative expected revision", () => {
    expect(
      movieStatePath({
        userId: 1,
        movieId: 2,
        resource: "watched",
        expectedRevision: -5,
      }),
    ).toBe("/api/users/1/movies/2/watched?expected_revision=0");
  });

  it("produces a v4-shaped key the API will accept as a UUID", () => {
    expect(newIdempotencyKey()).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });
});
