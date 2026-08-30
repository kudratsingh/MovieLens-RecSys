import { describe, expect, it, vi } from "vitest";

import {
  IDEMPOTENCY_CONFLICT,
  mutateMovieState,
  newIdempotencyKey,
  movieStatePath,
  REVISION_CONFLICT,
  TRANSITION_REFUSED,
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
 * A refusal and a race are different events with different recoveries, and the
 * API now says which is which in `ErrorResponse.code`: `transition_refused` on
 * a `422`, `revision_conflict` or `idempotency_conflict` on a `409`.
 *
 * This suite used to assert the six *sentences* the server raises, because the
 * client told the two apart by matching them (issue #74). Nothing here quotes
 * server prose any more — that was the defect, not the coverage.
 */
describe("telling a transition refusal apart from a concurrency conflict", () => {
  it("reads a coded refusal as a refusal", async () => {
    const detail = "a watched movie cannot be added to the watchlist";
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail, code: TRANSITION_REFUSED }, 422)),
    });

    expect(result).toMatchObject({ status: "refused", detail });
  });

  it.each([REVISION_CONFLICT, IDEMPOTENCY_CONFLICT])(
    "reads %s as a conflict the write path can recover",
    async (code) => {
      const detail = "a sentence the server is free to reword tomorrow";
      const result = await mutateMovieState({
        ...baseInput,
        fetchImpl: stubFetch(jsonResponse({ detail, code }, 409)),
      });

      expect(result).toMatchObject({ status: "conflict", detail });
    },
  );

  it("trusts the code over the status when the two disagree", async () => {
    // Not a shape the API produces. It is asserted because the code is the
    // contract: were a refusal ever to move to another status, no branch here
    // should need editing to keep telling the truth.
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail: "no", code: TRANSITION_REFUSED }, 409)),
    });

    expect(result.status).toBe("refused");
  });

  it("falls back to the status for a body carrying no code", async () => {
    const conflict = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail: "state revision 3 is stale" }, 409)),
    });
    const refusal = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ detail: "that is not allowed" }, 422)),
    });

    expect(conflict.status).toBe("conflict");
    expect(refusal.status).toBe("refused");
  });

  it("never shows a request-validation 422 as a product rule", async () => {
    // FastAPI answers a malformed request on the refusal's status, with
    // `detail` as a list of field errors and no code. Rendering that as the
    // reason a decision was declined would blame the viewer for our own
    // defect, so it falls through to the generic failure mapping.
    const validation = {
      detail: [
        {
          loc: ["body", "rating"],
          msg: "Input should be less than or equal to 5",
          type: "less_than_equal",
        },
      ],
    };
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse(validation, 422)),
    });

    expect(result.status).toBe("failed");
    expect(result.status === "failed" && result.failure.reason).toBe("bad-request");
  });

  it("keeps a 409 with nothing readable in it a conflict", async () => {
    // With no sentence to show, the generic recovery is all there is.
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(new Response(null, { status: 409 })),
    });

    expect(result).toMatchObject({ status: "conflict", detail: null });
  });

  it("does not strand a coded refusal that arrived with no sentence", async () => {
    // A refusal is announced in the API's own words, so one with nothing to
    // say is a malformed response rather than a rule to render with a blank
    // reason. It reports as a failure.
    const result = await mutateMovieState({
      ...baseInput,
      fetchImpl: stubFetch(jsonResponse({ code: TRANSITION_REFUSED }, 422)),
    });

    expect(result.status).toBe("failed");
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
