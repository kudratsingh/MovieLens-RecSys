import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { createBffMovieStateClient } from "@/lib/movie-state/client";

import { movieDetailResponse, movieState } from "./resource-fixtures";

const CSRF = { csrfToken: "csrf-token-value" };

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

/**
 * Answers the CSRF read first, then the request under test. A factory rather
 * than a value where a test makes the same request twice: a `Response` body can
 * only be read once.
 */
function fetchStub(response: (() => Response | Promise<Response>) | Response) {
  return vi.fn(async (url: string) => {
    if (url === "/api/auth/csrf") return jsonResponse(CSRF);
    return typeof response === "function" ? response() : response;
  }) as unknown as typeof fetch;
}

function calls(fetchImpl: typeof fetch) {
  return (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls;
}

/**
 * The Library's shape of a write: an intent-bound idempotency key and the
 * revision the row was rendered from. Every surface sends the same fields.
 */
const MUTATION = {
  userId: 900000101,
  movieId: 103,
  resource: "rating",
  method: "PUT",
  rating: 4.5,
  expectedRevision: 7,
  idempotencyKey: "6f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
  // The relay is a tab-local cache; these tests are about the request.
  store: null,
} as const;

describe("one write path serves every surface", () => {
  it("sends the canonical write boundary and never a caller credential", async () => {
    const committed = {
      outcome: "changed",
      replayed: false,
      request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
      state: { ...movieState, rating: 4.5, revision: 8 },
    };
    const fetchImpl = fetchStub(jsonResponse(committed));

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "committed", replayed: false });
    expect(result.status === "committed" && result.state.revision).toBe(8);

    const [url, init] = calls(fetchImpl)[1] as [string, RequestInit];
    expect(url).toBe("/api/users/900000101/movies/103/rating?expected_revision=7");
    expect(init.method).toBe("PUT");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe(JSON.stringify({ rating: 4.5 }));

    const headers = new Headers(init.headers);
    expect(headers.get("Idempotency-Key")).toBe(MUTATION.idempotencyKey);
    expect(headers.get("x-csrf-token")).toBe(CSRF.csrfToken);
    expect(headers.get("authorization")).toBeNull();
  });

  it("replays one intent under one key rather than writing twice", async () => {
    const fetchImpl = fetchStub(() =>
      jsonResponse({
        outcome: "no_change",
        replayed: true,
        request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
        state: movieState,
      }),
    );
    const client = createBffMovieStateClient(fetchImpl);

    await client.mutate(MUTATION);
    const retry = await client.mutate(MUTATION);

    const keys = calls(fetchImpl)
      .filter(([url]) => String(url).includes("/movies/"))
      .map(([, init]) => new Headers((init as RequestInit).headers).get("Idempotency-Key"));
    expect(keys).toEqual([MUTATION.idempotencyKey, MUTATION.idempotencyKey]);
    expect(retry).toMatchObject({ status: "committed", replayed: true });
  });

  it("mints a fresh key when the caller does not bind one to an intent", async () => {
    const fetchImpl = fetchStub(() =>
      jsonResponse({
        outcome: "changed",
        replayed: false,
        request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
        state: movieState,
      }),
    );
    const client = createBffMovieStateClient(fetchImpl);
    const unbound = { ...MUTATION, idempotencyKey: undefined };

    await client.mutate(unbound);
    await client.mutate(unbound);

    const keys = calls(fetchImpl)
      .filter(([url]) => String(url).includes("/movies/"))
      .map(([, init]) => new Headers((init as RequestInit).headers).get("Idempotency-Key"));
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("sends no body for a delete", async () => {
    const fetchImpl = fetchStub(
      jsonResponse({
        outcome: "changed",
        replayed: false,
        request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
        state: movieState,
      }),
    );

    await createBffMovieStateClient(fetchImpl).mutate({
      ...MUTATION,
      resource: "watched",
      method: "DELETE",
    });

    const [, init] = calls(fetchImpl)[1] as [string, RequestInit];
    expect(init.body).toBeUndefined();
  });

  it("names a revision conflict as its own outcome rather than a bad request", async () => {
    // Quoted from `StateRevisionConflictError` in `src/serving/feedback.py`:
    // the client tells a race apart from a refused transition by this body,
    // so an invented sentence here would not exercise the split.
    const fetchImpl = fetchStub(
      jsonResponse({ detail: "state revision 7 is stale; current revision is 9" }, { status: 409 }),
    );

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "conflict", detail: "state revision 7 is stale; current revision is 9" });
  });

  it("maps an expired session during a write the same way a read does", async () => {
    const fetchImpl = fetchStub(
      jsonResponse({ detail: "Your session has expired." }, { status: 401 }),
    );

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result.status).toBe("failed");
    expect(result.status === "failed" && result.failure).toMatchObject({
      status: "auth-expired",
      reason: "session-expired",
    });
  });

  it("refuses a response that does not match the published contract", async () => {
    const fetchImpl = fetchStub(jsonResponse({ state: { revision: "eight" } }));

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result.status === "failed" && result.failure).toMatchObject({
      status: "upstream-error",
      reason: "invalid-payload",
    });
  });

  it("reports a transport failure as retryable rather than as a rejected write", async () => {
    const fetchImpl = fetchStub(() => Promise.reject(new TypeError("fetch failed")));

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result.status === "failed" && result.failure).toMatchObject({
      reason: "network",
      retryable: true,
    });
  });
});

describe("a stale revision is corrected rather than discarded", () => {
  /**
   * The first press of a Discover or Quick Picks control can only assert
   * revision 0, because a recommendation carries no state. Anything that has
   * ever been written and reverted sits higher than that, so without the replay
   * the viewer's press is silently thrown away and only the second one commits.
   */
  function conflictThenCommit(canonicalRevision: number) {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      if (url === "/api/auth/csrf") return jsonResponse(CSRF);
      if (url.startsWith("/api/users/900000101/movies/103/rating")) {
        return url.includes(`expected_revision=${canonicalRevision}`)
          ? jsonResponse({
              outcome: "changed",
              replayed: false,
              request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
              state: { ...movieState, revision: canonicalRevision + 1 },
            })
          : jsonResponse({ detail: "state revision is stale" }, { status: 409 });
      }
      return jsonResponse({
        ...movieDetailResponse,
        item: {
          ...movieDetailResponse.item,
          state: { ...movieState, revision: canonicalRevision },
        },
      });
    }) as unknown as typeof fetch;
    return { calls, fetchImpl };
  }

  function mutationCalls(calls: { url: string; init: RequestInit }[]) {
    return calls.filter(({ url }) => url.includes("/movies/103/rating"));
  }

  it("replays the same intent against the revision the API reports", async () => {
    const { calls, fetchImpl } = conflictThenCommit(9);

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "committed" });
    expect(result.status === "committed" && result.state.revision).toBe(10);
    const writes = mutationCalls(calls);
    expect(writes.map(({ url }) => url)).toEqual([
      "/api/users/900000101/movies/103/rating?expected_revision=7",
      "/api/users/900000101/movies/103/rating?expected_revision=9",
    ]);
    // One decision, one key: the API replays a lost commit instead of writing
    // a second feedback event for the same press.
    const keys = writes.map(({ init }) =>
      new Headers(init.headers).get("Idempotency-Key"),
    );
    expect(keys).toEqual([MUTATION.idempotencyKey, MUTATION.idempotencyKey]);
  });

  it("carries one key across both attempts even when the caller binds none", async () => {
    const { calls, fetchImpl } = conflictThenCommit(9);

    await createBffMovieStateClient(fetchImpl).mutate({
      ...MUTATION,
      idempotencyKey: undefined,
    });

    const keys = mutationCalls(calls).map(({ init }) =>
      new Headers(init.headers).get("Idempotency-Key"),
    );
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toBe(keys[0]);
  });

  it("replays exactly once and reports a second conflict as a conflict", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (url: string) => {
      calls.push(url);
      if (url === "/api/auth/csrf") return jsonResponse(CSRF);
      if (url.includes("/rating")) {
        return jsonResponse({ detail: "idempotency key was already used for another mutation" }, { status: 409 });
      }
      return jsonResponse(movieDetailResponse);
    }) as unknown as typeof fetch;

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result.status).toBe("conflict");
    // The canonical record rides along, so the caller corrects its control
    // without issuing a read of its own.
    expect(result.status === "conflict" && result.canonical?.revision).toBe(
      movieDetailResponse.item.state?.revision,
    );
    expect(calls.filter((url) => url.includes("/rating"))).toHaveLength(2);
  });

  it("does not replay a revision assertion the API has already answered", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (url: string) => {
      calls.push(url);
      if (url === "/api/auth/csrf") return jsonResponse(CSRF);
      if (url.includes("/rating")) {
        return jsonResponse({ detail: "state revision 7 is stale; current revision is 9" }, { status: 409 });
      }
      // The stored revision is the one the caller already sent, so replaying
      // it would only produce the same answer.
      return jsonResponse({
        ...movieDetailResponse,
        item: { ...movieDetailResponse.item, state: { ...movieState, revision: 7 } },
      });
    }) as unknown as typeof fetch;

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result.status).toBe("conflict");
    expect(calls.filter((url) => url.includes("/rating"))).toHaveLength(1);
  });

  it("stands the conflict up plainly when the canonical read answers nothing", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (url: string) => {
      calls.push(url);
      if (url === "/api/auth/csrf") return jsonResponse(CSRF);
      if (url.includes("/rating")) {
        return jsonResponse({ detail: "state revision 7 is stale; current revision is 9" }, { status: 409 });
      }
      return jsonResponse({ detail: "gone" }, { status: 502 });
    }) as unknown as typeof fetch;

    const result = await createBffMovieStateClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "conflict", canonical: null });
    expect(calls.filter((url) => url.includes("/rating"))).toHaveLength(1);
  });
});

describe("a transition the API refuses is not a race", () => {
  it("reports the refusal without a canonical read or a replay", async () => {
    // Quoted from `InvalidStateTransitionError` in `src/serving/feedback.py`.
    // The API answers this on the same 409 as a stale revision, and the older
    // client mapped both to `conflict` — so a viewer who pressed `Watchlist`
    // on a title they had already watched was told it "changed somewhere else
    // before this saved", and the client spent a read plus a replay proving
    // otherwise.
    const detail = "a watched movie cannot be added to the watchlist";
    const seen: string[] = [];
    const fetchImpl = vi.fn(async (url: string) => {
      seen.push(url);
      if (url === "/api/auth/csrf") return jsonResponse(CSRF);
      return jsonResponse({ detail }, { status: 409 });
    }) as unknown as typeof fetch;

    const result = await createBffMovieStateClient(fetchImpl).mutate({
      ...MUTATION,
      resource: "watchlist",
      method: "PUT",
      rating: undefined,
    });

    expect(result).toMatchObject({ status: "refused", detail });
    // One write attempt, and no canonical read behind it.
    expect(seen.filter((url) => url.includes("/watchlist"))).toHaveLength(1);
    expect(seen.some((url) => /\/movies\/\d+$/.test(url))).toBe(false);
  });
});

describe("recovering from a conflict", () => {
  it("reads the canonical record so the next write asserts a real revision", async () => {
    const fetchImpl = fetchStub(jsonResponse(movieDetailResponse));

    const state = await createBffMovieStateClient(fetchImpl).readState(900000101, 101);

    expect(calls(fetchImpl)[0][0]).toBe("/api/users/900000101/movies/101");
    expect(state?.revision).toBe(movieDetailResponse.item.state?.revision);
  });

  it("answers with nothing rather than a guess when the read fails", async () => {
    const fetchImpl = fetchStub(jsonResponse({ detail: "gone" }, { status: 502 }));

    expect(
      await createBffMovieStateClient(fetchImpl).readState(900000101, 101),
    ).toBeNull();
  });
});

describe("the shared write path cannot reach recorded data", () => {
  it("imports no fixture module", () => {
    for (const file of ["lib/movie-state/client.ts", "lib/movie-state/mutate.ts"]) {
      const source = readFileSync(resolve(process.cwd(), file), "utf8");
      expect(source).not.toMatch(/from\s+"@\/lib\/fixtures/);
      expect(source).not.toMatch(/from\s+"@\/lib\/resources\/fixture-gate"/);
      expect(source).not.toMatch(/MOVIELENS_UI_FIXTURE_MODE/);
    }
  });
});
