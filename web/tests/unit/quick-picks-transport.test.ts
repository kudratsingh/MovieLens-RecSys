import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { beforeEach, describe, expect, it, vi } from "vitest";

import { createFixtureQuickPickTransport } from "@/lib/quick-picks/fixture-transport";
import { fixtureQuickPickResponse } from "@/lib/quick-picks/fixtures";
import {
  createLiveQuickPickTransport,
  type QuickPickQueuePayload,
} from "@/lib/quick-picks/transport";
import { readyState } from "@/lib/resources/state";

import { movieState } from "./resource-fixtures";

const payload: QuickPickQueuePayload = {
  queue: readyState("recommendations", fixtureQuickPickResponse(), "req-1", "recorded-contract-fixture"),
  evidence: {},
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function transportWith(fetchImpl: typeof fetch) {
  return createLiveQuickPickTransport({
    fetchImpl,
    loadQueue: () => Promise.resolve(payload),
    loadSeedTitle: () => Promise.resolve(null),
    userId: 900000101,
  });
}

beforeEach(() => {
  vi.stubGlobal("crypto", {
    ...globalThis.crypto,
    randomUUID: () => "11111111-2222-4333-8444-555555555555",
  });
});

describe("the live mutation path", () => {
  it("reuses the Bundle 2 feedback boundary with a CSRF token and an idempotency key", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/auth/csrf") return jsonResponse({ csrfToken: "csrf-token" });
      return jsonResponse({
        outcome: "changed",
        replayed: false,
        request_id: "11111111-2222-4333-8444-555555555555",
        state: { ...movieState, dismissed_at: "2026-08-21T12:00:00Z" },
      });
    }) as unknown as typeof fetch;

    const outcome = await transportWith(fetchImpl).commit({
      action: "dismiss",
      movieId: 105,
      rating: null,
      expectedRevision: null,
    });

    expect(outcome.ok).toBe(true);
    const mutation = calls[1];
    expect(mutation.url).toBe("/api/users/900000101/movies/105/dismissal");
    expect(mutation.init?.method).toBe("PUT");
    const headers = mutation.init?.headers as Record<string, string>;
    expect(headers["x-csrf-token"]).toBe("csrf-token");
    expect(headers["Idempotency-Key"]).toBe("11111111-2222-4333-8444-555555555555");
    expect(mutation.init?.body).toBeUndefined();
  });

  it("sends a rating as one watched-implying write and asserts an observed revision", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url === "/api/auth/csrf") return jsonResponse({ csrfToken: "csrf-token" });
      expect(init?.body).toBe(JSON.stringify({ rating: 4 }));
      return jsonResponse({
        outcome: "changed",
        replayed: false,
        request_id: "11111111-2222-4333-8444-555555555555",
        state: movieState,
      });
    }) as unknown as typeof fetch;

    await transportWith(fetchImpl).commit({
      action: "watched",
      movieId: 105,
      rating: 4,
      expectedRevision: 3,
    });

    expect(calls[1]).toBe("/api/users/900000101/movies/105/rating?expected_revision=3");
  });

  it("surfaces the API's own conflict detail instead of a generic failure", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) =>
      String(input) === "/api/auth/csrf"
        ? jsonResponse({ csrfToken: "csrf-token" })
        : jsonResponse({ detail: "a watched movie cannot be added to the watchlist" }, 409),
    ) as unknown as typeof fetch;

    const outcome = await transportWith(fetchImpl).commit({
      action: "watchlist",
      movieId: 105,
      rating: null,
      expectedRevision: null,
    });

    expect(outcome).toEqual({
      ok: false,
      message: "a watched movie cannot be added to the watchlist",
    });
  });

  it("treats a 200 that does not match the mutation contract as a failure", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) =>
      String(input) === "/api/auth/csrf"
        ? jsonResponse({ csrfToken: "csrf-token" })
        : jsonResponse({ state: { movie_id: 105 } }),
    ) as unknown as typeof fetch;

    const outcome = await transportWith(fetchImpl).commit({
      action: "dismiss",
      movieId: 105,
      rating: null,
      expectedRevision: null,
    });

    expect(outcome.ok).toBe(false);
  });

  it("never lets a lost network turn into a claimed save", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;

    const outcome = await transportWith(fetchImpl).commit({
      action: "watched",
      movieId: 105,
      rating: null,
      expectedRevision: null,
    });

    expect(outcome).toEqual({ ok: false, message: "fetch failed" });
  });
});

describe("the recorded transport mirrors the API's transitions", () => {
  it("implies watched from a rating and clears the watchlist", async () => {
    const transport = createFixtureQuickPickTransport({ initial: payload });
    await transport.commit({ action: "watchlist", movieId: 105, rating: null, expectedRevision: null });
    const outcome = await transport.commit({
      action: "watched",
      movieId: 105,
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
    await transport.commit({ action: "watched", movieId: 105, rating: null, expectedRevision: null });
    await transport.commit({ action: "dismiss", movieId: 102, rating: null, expectedRevision: null });
    const refreshed = await transport.refresh();

    const items =
      refreshed.queue.status === "ready" ? refreshed.queue.data.items.map((item) => item.movie_id) : [];
    expect(items).not.toContain(105);
    expect(items).not.toContain(102);
    expect(
      refreshed.queue.status === "ready" &&
        refreshed.queue.data.serving_policy.positive_signal_count,
    ).toBe(fixtureQuickPickResponse().serving_policy.positive_signal_count + 1);
  });

  it("fails every commit when failure injection is on", async () => {
    const transport = createFixtureQuickPickTransport({ failCommits: true, initial: payload });
    const outcome = await transport.commit({
      action: "dismiss",
      movieId: 105,
      rating: null,
      expectedRevision: null,
    });

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
