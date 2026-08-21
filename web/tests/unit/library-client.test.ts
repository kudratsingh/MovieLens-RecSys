import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { createBffLibraryClient, libraryReadUrl } from "@/lib/library/client";

import { libraryResponse, movieState } from "./resource-fixtures";

const CSRF = { csrfToken: "csrf-token-value" };

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

/** Answers the CSRF read first, then the request under test. */
function fetchStub(response: Response | (() => Promise<Response>)) {
  return vi.fn(async (url: string) => {
    if (url === "/api/auth/csrf") return jsonResponse(CSRF);
    return typeof response === "function" ? response() : response;
  }) as unknown as typeof fetch;
}

const MUTATION = {
  userId: 900000101,
  movieId: 103,
  resource: "rating",
  method: "PUT",
  rating: 4.5,
  expectedRevision: 7,
  idempotencyKey: "6f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
} as const;

describe("library read URLs", () => {
  it("forwards tab, sort, filter, cursor, and a bounded page size", () => {
    expect(
      libraryReadUrl({
        userId: 900000104,
        tab: "history",
        sort: "title",
        query: "burning",
        cursor: "opaque-2",
        limit: 12,
      }),
    ).toBe(
      "/api/users/900000104/library?tab=history&sort=title&limit=12&q=burning&cursor=opaque-2",
    );
  });

  it("omits an empty filter and a first-page cursor rather than sending blanks", () => {
    expect(
      libraryReadUrl({
        userId: 1,
        tab: "rated",
        sort: "recent",
        query: "",
        cursor: null,
        limit: 24,
      }),
    ).toBe("/api/users/1/library?tab=rated&sort=recent&limit=24");
  });
});

describe("library reads go through the shared boundary", () => {
  it("validates the payload and reports the region's own request ID", async () => {
    const fetchImpl = fetchStub(
      new Response(JSON.stringify(libraryResponse), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
        },
      }),
    );

    const state = await createBffLibraryClient(fetchImpl).readLibrary({
      userId: 900000101,
      tab: "rated",
      sort: "recent",
      query: "",
      cursor: null,
    });

    expect(state.status).toBe("ready");
    expect(state.status === "ready" && state.requestId).toBe(
      "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
    );
    expect(state.status === "ready" && state.source).toBe("live");
  });
});

describe("library mutations", () => {
  it("sends the canonical write boundary and never a caller credential", async () => {
    const committed = {
      outcome: "changed",
      replayed: false,
      request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
      state: { ...movieState, rating: 4.5, revision: 8 },
    };
    const fetchImpl = fetchStub(jsonResponse(committed));

    const result = await createBffLibraryClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "committed", replayed: false });
    expect(result.status === "committed" && result.state.revision).toBe(8);

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(url).toBe(
      "/api/users/900000101/movies/103/rating?expected_revision=7",
    );
    expect(init.method).toBe("PUT");
    expect(init.credentials).toBe("same-origin");
    expect(init.body).toBe(JSON.stringify({ rating: 4.5 }));
    expect(init.headers["Idempotency-Key"]).toBe(MUTATION.idempotencyKey);
    expect(init.headers["x-csrf-token"]).toBe(CSRF.csrfToken);
    expect(Object.keys(init.headers).map((name) => name.toLowerCase())).not.toContain(
      "authorization",
    );
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

    await createBffLibraryClient(fetchImpl).mutate({
      ...MUTATION,
      resource: "watched",
      method: "DELETE",
    });

    const [, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(init.body).toBeUndefined();
  });

  it("names a revision conflict as its own outcome rather than a bad request", async () => {
    const fetchImpl = fetchStub(
      jsonResponse({ detail: "Stale state revision" }, { status: 409 }),
    );

    const result = await createBffLibraryClient(fetchImpl).mutate(MUTATION);

    expect(result.status).toBe("conflict");
  });

  it("maps an expired session during a write the same way a read does", async () => {
    const fetchImpl = fetchStub(
      jsonResponse({ detail: "Your session has expired." }, { status: 401 }),
    );

    const result = await createBffLibraryClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "auth-expired", reason: "session-expired" });
  });

  it("refuses a response that does not match the published contract", async () => {
    const fetchImpl = fetchStub(jsonResponse({ state: { revision: "eight" } }));

    const result = await createBffLibraryClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({
      status: "upstream-error",
      reason: "invalid-payload",
    });
  });

  it("reports a transport failure as retryable rather than as a rejected write", async () => {
    const fetchImpl = fetchStub(() => Promise.reject(new TypeError("fetch failed")));

    const result = await createBffLibraryClient(fetchImpl).mutate(MUTATION);

    expect(result).toMatchObject({ status: "upstream-error", reason: "network" });
    expect(result.status === "upstream-error" && result.retryable).toBe(true);
  });
});

describe("the live Library client cannot reach recorded data", () => {
  it("imports no fixture module", () => {
    const source = readFileSync(
      resolve(process.cwd(), "lib/library/client.ts"),
      "utf8",
    );

    expect(source).not.toMatch(/from\s+"@\/lib\/fixtures/);
    expect(source).not.toMatch(/from\s+"@\/lib\/resources\/fixture-gate"/);
    expect(source).not.toMatch(/MOVIELENS_UI_FIXTURE_MODE/);
  });
});
