import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { readCommittedStates } from "@/lib/movie-state/committed-store";
import { createFixtureQuickPickTransport } from "@/lib/quick-picks/fixture-transport";
import { fixtureQuickPickResponse } from "@/lib/quick-picks/fixtures";
import {
  createLiveQuickPickTransport,
  type QuickPickQueuePayload,
} from "@/lib/quick-picks/transport";
import { readyState } from "@/lib/resources/state";

import { catalogResponse, movieState } from "./resource-fixtures";

const USER_ID = 900000101;
const MOVIE_ID = 105;

const payload: QuickPickQueuePayload = {
  queue: readyState(
    "recommendations",
    fixtureQuickPickResponse(),
    "req-1",
    "recorded-contract-fixture",
  ),
  evidence: {},
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function memoryStore() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
  };
}

type Call = { url: string; init?: RequestInit };

/**
 * Stands in for the BFF: a CSRF token, a canonical mutation response, and the
 * movie-detail read the conflict path and the seed-title lookup share.
 */
function stubBff(options: {
  mutation?: (call: Call) => Response;
  detailState?: typeof movieState | null;
} = {}) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === "/api/auth/csrf") return jsonResponse({ csrfToken: "csrf-token" });
    if (/\/movies\/\d+$/.test(url)) {
      return jsonResponse({
        ...catalogResponse,
        item: {
          ...catalogResponse.items[0],
          movie_id: MOVIE_ID,
          title: "Memories of Murder",
          state: options.detailState === undefined ? movieState : options.detailState,
          // Detail's item type carries the enriched TMDB block, required and
          // nullable. Quick Picks reads this response only for the committed
          // state and the seed title, but the guard checks the whole record.
          details: null,
        },
      });
    }
    return (
      options.mutation?.({ url, init }) ??
      jsonResponse({
        outcome: "changed",
        replayed: false,
        request_id: "11111111-2222-4333-8444-555555555555",
        state: { ...movieState, movie_id: MOVIE_ID, revision: 4 },
      })
    );
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

function transportWith(fetchImpl: typeof fetch, sessionStore = memoryStore()) {
  return {
    sessionStore,
    transport: createLiveQuickPickTransport({
      fetchImpl,
      loadQueue: () => Promise.resolve(payload),
      sessionStore,
      userId: USER_ID,
    }),
  };
}

const dismiss = {
  action: "dismiss" as const,
  movieId: MOVIE_ID,
  rating: null,
  expectedRevision: null,
};

describe("the live write path", () => {
  it("goes through the canonical movie-state mutation, headers and all", async () => {
    const { calls, fetchImpl } = stubBff();
    const outcome = await transportWith(fetchImpl).transport.commit(dismiss);

    expect(outcome.ok).toBe(true);
    const mutation = calls.at(-1);
    expect(mutation?.url).toBe(
      `/api/users/${USER_ID}/movies/${MOVIE_ID}/dismissal?expected_revision=0`,
    );
    expect(mutation?.init?.method).toBe("PUT");
    const headers = new Headers(mutation?.init?.headers);
    expect(headers.get("x-csrf-token")).toBe("csrf-token");
    expect(headers.get("idempotency-key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(headers.get("x-request-id")).toBeTruthy();
    expect(mutation?.init?.body).toBeUndefined();
  });

  it("sends a rating as one watched-implying write", async () => {
    const { calls, fetchImpl } = stubBff();
    await transportWith(fetchImpl).transport.commit({
      action: "watched",
      movieId: MOVIE_ID,
      rating: 4,
      expectedRevision: null,
    });

    const mutation = calls.at(-1);
    expect(mutation?.url).toContain(`/movies/${MOVIE_ID}/rating?expected_revision=0`);
    expect(mutation?.init?.body).toBe(JSON.stringify({ rating: 4 }));
  });

  it("asserts the revision the machine observed for an undo", async () => {
    const { calls, fetchImpl } = stubBff();
    await transportWith(fetchImpl).transport.commit({
      action: "undo-dismiss",
      movieId: MOVIE_ID,
      rating: null,
      expectedRevision: 7,
    });

    const mutation = calls.at(-1);
    expect(mutation?.url).toBe(
      `/api/users/${USER_ID}/movies/${MOVIE_ID}/dismissal?expected_revision=7`,
    );
    expect(mutation?.init?.method).toBe("DELETE");
  });

  it("relays a committed state so the next write asserts a server-issued revision", async () => {
    const { fetchImpl, calls } = stubBff();
    const { transport, sessionStore } = transportWith(fetchImpl);

    await transport.commit(dismiss);
    expect(readCommittedStates(sessionStore, USER_ID).get(MOVIE_ID)?.revision).toBe(4);

    await transport.commit(dismiss);
    expect(calls.at(-1)?.url).toContain("expected_revision=4");
  });

  it("reads a relayed revision another route committed rather than asserting zero", async () => {
    const { fetchImpl: seeding } = stubBff();
    const store = memoryStore();
    await transportWith(seeding, store).transport.commit(dismiss);

    // A fresh transport, as if the viewer arrived from Browse or detail.
    const { calls, fetchImpl } = stubBff();
    await transportWith(fetchImpl, store).transport.commit(dismiss);

    expect(calls.at(-1)?.url).toContain("expected_revision=4");
  });

  it("commits the first press of a card whose title already has state", async () => {
    // The queue carries no revision, so the first write asserts 0; anything
    // written and reverted earlier sits higher. Without the replay the whole
    // decision — the card advance included — is silently discarded.
    const { calls, fetchImpl } = stubBff({
      mutation: ({ url }) =>
        url.includes("expected_revision=9")
          ? jsonResponse({
              outcome: "changed",
              replayed: false,
              request_id: "11111111-2222-4333-8444-555555555555",
              state: { ...movieState, movie_id: MOVIE_ID, revision: 10 },
            })
          : jsonResponse({ detail: "state revision 0 is stale" }, 409),
      detailState: { ...movieState, movie_id: MOVIE_ID, revision: 9 },
    });

    const outcome = await transportWith(fetchImpl).transport.commit(dismiss);

    expect(outcome).toMatchObject({ ok: true });
    expect(outcome.ok && outcome.state.revision).toBe(10);
    const writes = calls.filter((call) => call.url.includes("/dismissal"));
    expect(writes.map((call) => call.url)).toEqual([
      `/api/users/${USER_ID}/movies/${MOVIE_ID}/dismissal?expected_revision=0`,
      `/api/users/${USER_ID}/movies/${MOVIE_ID}/dismissal?expected_revision=9`,
    ]);
  });

  it("keeps one idempotency key across the re-press of a failed decision", async () => {
    const { calls, fetchImpl } = stubBff({
      mutation: () => jsonResponse({ detail: "the API is down" }, 502),
    });
    const { transport } = transportWith(fetchImpl);

    await transport.commit(dismiss);
    await transport.commit(dismiss);

    const keys = calls
      .filter((call) => call.url.includes("/dismissal"))
      .map((call) => new Headers(call.init?.headers).get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it("treats a different decision on the same card as a different intent", async () => {
    const { calls, fetchImpl } = stubBff({
      mutation: () => jsonResponse({ detail: "the API is down" }, 502),
    });
    const { transport } = transportWith(fetchImpl);

    await transport.commit(dismiss);
    await transport.commit({ ...dismiss, action: "watchlist" });

    const keys = calls
      .filter((call) => /\/(dismissal|watchlist)\?/.test(call.url))
      .map((call) => new Headers(call.init?.headers).get("Idempotency-Key"));
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("mints a new key once a decision has committed", async () => {
    const { calls, fetchImpl } = stubBff();
    const { transport } = transportWith(fetchImpl);

    await transport.commit(dismiss);
    await transport.commit(dismiss);

    const keys = calls
      .filter((call) => call.url.includes("/dismissal"))
      .map((call) => new Headers(call.init?.headers).get("Idempotency-Key"));
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("turns a revision conflict into a correction the viewer can retry", async () => {
    const { calls, fetchImpl } = stubBff({
      mutation: () => jsonResponse({ detail: "state revision 0 is stale" }, 409),
      detailState: { ...movieState, movie_id: MOVIE_ID, revision: 9 },
    });
    const { transport, sessionStore } = transportWith(fetchImpl);

    const outcome = await transport.commit(dismiss);

    expect(outcome).toMatchObject({ ok: false, conflict: true });
    expect(outcome.ok === false && outcome.message).toContain("try again");
    // The canonical record was re-read, so the retry asserts a real revision.
    expect(calls.some((call) => /\/movies\/\d+$/.test(call.url))).toBe(true);
    expect(readCommittedStates(sessionStore, USER_ID).get(MOVIE_ID)?.revision).toBe(9);
  });

  it("states the rule when the API refuses the transition itself", async () => {
    // Quoted from `InvalidStateTransitionError` in `src/serving/feedback.py`.
    // It arrives on the same 409 as a stale revision, and this deck used to
    // tell the viewer the title "changed somewhere else before this saved" and
    // invite a retry that could never work.
    const rule = "a watched movie cannot be added to the watchlist";
    const { calls, fetchImpl } = stubBff({
      mutation: () => jsonResponse({ detail: rule }, 409),
    });
    const { transport, sessionStore } = transportWith(fetchImpl);

    const outcome = await transport.commit({ ...dismiss, action: "watchlist" });

    expect(outcome).toMatchObject({ ok: false, refused: true });
    expect(outcome.ok === false && outcome.conflict).toBeUndefined();
    expect(outcome.ok === false && outcome.message).toBe(
      `That decision was not recorded. ${rule}.`,
    );
    expect(outcome.ok === false && outcome.message).not.toContain("try again");
    // Nothing was written, so nothing was re-read and nothing was relayed.
    expect(calls.some((call) => /\/movies\/\d+$/.test(call.url))).toBe(false);
    expect(readCommittedStates(sessionStore, USER_ID).get(MOVIE_ID)).toBeUndefined();
  });

  it("never lets a lost network turn into a claimed save", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/auth/csrf") return jsonResponse({ csrfToken: "t" });
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;

    const outcome = await transportWith(fetchImpl).transport.commit(dismiss);

    expect(outcome.ok).toBe(false);
    expect(outcome.ok === false && outcome.message).toContain("did not save");
  });

  it("refuses a rating the API and database would reject before sending it", async () => {
    const { calls, fetchImpl } = stubBff();
    const outcome = await transportWith(fetchImpl).transport.commit({
      action: "watched",
      movieId: MOVIE_ID,
      rating: 4.2,
      expectedRevision: null,
    });

    expect(outcome.ok).toBe(false);
    expect(calls).toHaveLength(0);
  });

  it("resolves a seed title through the shared movie-detail route", async () => {
    const { calls, fetchImpl } = stubBff();
    const title = await transportWith(fetchImpl).transport.resolveSeedTitle(103);

    expect(title).toBe("Memories of Murder");
    expect(calls.at(-1)?.url).toBe(`/api/users/${USER_ID}/movies/103`);
  });
});

describe("the recorded transport mirrors the API's transitions", () => {
  it("implies watched from a rating and clears the watchlist", async () => {
    const transport = createFixtureQuickPickTransport({ initial: payload });
    await transport.commit({
      action: "watchlist",
      movieId: MOVIE_ID,
      rating: null,
      expectedRevision: null,
    });
    const outcome = await transport.commit({
      action: "watched",
      movieId: MOVIE_ID,
      rating: 3,
      expectedRevision: null,
    });

    expect(outcome.ok && outcome.state.watched_at).not.toBeNull();
    expect(outcome.ok && outcome.state.rating).toBe(3);
    expect(outcome.ok && outcome.state.watchlisted_at).toBeNull();
    expect(outcome.ok && outcome.state.revision).toBe(2);
  });

  it("drops decided titles from the refreshed queue and moves the signal count", async () => {
    const transport = createFixtureQuickPickTransport({ initial: payload });
    await transport.commit({
      action: "watched",
      movieId: MOVIE_ID,
      rating: null,
      expectedRevision: null,
    });
    await transport.commit({
      action: "dismiss",
      movieId: 102,
      rating: null,
      expectedRevision: null,
    });
    const refreshed = await transport.refresh();

    const items =
      refreshed.queue.status === "ready"
        ? refreshed.queue.data.items.map((item) => item.movie_id)
        : [];
    expect(items).not.toContain(MOVIE_ID);
    expect(items).not.toContain(102);
    expect(
      refreshed.queue.status === "ready" &&
        refreshed.queue.data.serving_policy.positive_signal_count,
    ).toBe(fixtureQuickPickResponse().serving_policy.positive_signal_count + 1);
  });

  it("fails every commit when failure injection is on", async () => {
    const transport = createFixtureQuickPickTransport({ failCommits: true, initial: payload });
    const outcome = await transport.commit(dismiss);

    expect(outcome.ok).toBe(false);
  });
});

describe("the live Quick Picks path cannot reach recorded data", () => {
  function moduleSource(relativePath: string) {
    return readFileSync(resolve(process.cwd(), relativePath), "utf8");
  }

  it.each([
    "lib/quick-picks/transport.ts",
    "lib/quick-picks/server.ts",
    "components/quick-picks/live-quick-picks.tsx",
    "app/quick-picks/page.tsx",
    "app/quick-picks/actions.ts",
  ])("keeps %s free of fixture imports", (path) => {
    const source = moduleSource(path);
    expect(source).not.toMatch(/from\s+"@\/lib\/fixtures/);
    expect(source).not.toMatch(/from\s+"@\/lib\/quick-picks\/fixtures?"/);
    expect(source).not.toMatch(/fixture-transport/);
    expect(source).not.toMatch(/from\s+"@\/lib\/resources\/fixture-gate"/);
  });
});
