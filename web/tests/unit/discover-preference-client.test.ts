import { describe, expect, it, vi } from "vitest";

import {
  createBffPreferenceClient,
  preferencePath,
} from "@/lib/discover/preference-client";
import {
  featuredPreferencesOff,
  featuredPreferencesOn,
} from "@/lib/fixtures/discover-fixtures";

type Call = { url: string; method: string; headers: Headers; body?: string };

function transport(
  handler: (call: Call) => Response,
): { calls: Call[]; fetchImpl: typeof fetch } {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/auth/csrf")) {
      return Response.json({ csrfToken: "csrf-token" });
    }
    const call: Call = {
      url,
      method: init?.method ?? "GET",
      headers: new Headers(init?.headers),
      body: typeof init?.body === "string" ? init.body : undefined,
    };
    calls.push(call);
    return handler(call);
  });
  return { calls, fetchImpl: fetchImpl as unknown as typeof fetch };
}

describe("writing the Featured picks preference", () => {
  it("asserts the revision it rendered and sends the whole object", async () => {
    const { calls, fetchImpl } = transport(() =>
      Response.json({ outcome: "changed", preferences: featuredPreferencesOff }),
    );

    const result = await createBffPreferenceClient(fetchImpl).set({
      userId: 900000102,
      featureWatchlistedTitles: false,
      expectedRevision: 0,
    });

    expect(result).toMatchObject({
      status: "committed",
      outcome: "changed",
      preference: { featureWatchlistedTitles: false, revision: 1 },
    });
    expect(calls[0].url).toBe("/api/users/900000102/preferences?expected_revision=0");
    expect(calls[0].method).toBe("PUT");
    expect(JSON.parse(calls[0].body ?? "{}")).toEqual({
      feature_watchlisted_titles: false,
    });
  });

  it("carries the double-submit CSRF token and no credential of its own", async () => {
    const { calls, fetchImpl } = transport(() =>
      Response.json({ outcome: "changed", preferences: featuredPreferencesOff }),
    );

    await createBffPreferenceClient(fetchImpl).set({
      userId: 900000102,
      featureWatchlistedTitles: false,
      expectedRevision: 0,
    });

    expect(calls[0].headers.get("x-csrf-token")).toBe("csrf-token");
    // The access token lives in the BFF session; this request must never
    // contribute one of its own.
    expect(calls[0].headers.has("authorization")).toBe(false);
    expect(calls[0].headers.get("X-Request-ID")).toBeTruthy();
  });

  it("re-reads and replays once when somebody committed first", async () => {
    const { calls, fetchImpl } = transport((call) => {
      if (call.method === "GET") return Response.json(featuredPreferencesOff);
      return call.url.endsWith("expected_revision=0")
        ? Response.json(
            { detail: "state revision 0 is stale; current revision is 1" },
            { status: 409 },
          )
        : Response.json({ outcome: "changed", preferences: featuredPreferencesOn });
    });

    const result = await createBffPreferenceClient(fetchImpl).set({
      userId: 900000102,
      featureWatchlistedTitles: true,
      expectedRevision: 0,
    });

    expect(result.status).toBe("committed");
    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      "PUT /api/users/900000102/preferences?expected_revision=0",
      "GET /api/users/900000102/preferences",
      "PUT /api/users/900000102/preferences?expected_revision=1",
    ]);
  });

  it("reports a conflict it could not resolve, with what is stored", async () => {
    // The stored revision is the one that was just refused, so replaying it
    // would only earn the same answer. That is a genuine conflict.
    const { calls, fetchImpl } = transport((call) =>
      call.method === "GET"
        ? Response.json(featuredPreferencesOn)
        : Response.json({ detail: "state revision 0 is stale" }, { status: 409 }),
    );

    const result = await createBffPreferenceClient(fetchImpl).set({
      userId: 900000102,
      featureWatchlistedTitles: false,
      expectedRevision: 0,
    });

    expect(result).toMatchObject({
      status: "conflict",
      canonical: { featureWatchlistedTitles: true, revision: 0 },
    });
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
  });

  it("treats an unreadable answer as a failure rather than a commit", async () => {
    const { fetchImpl } = transport(() =>
      Response.json({ preferences: { nonsense: true } }),
    );

    const result = await createBffPreferenceClient(fetchImpl).set({
      userId: 900000102,
      featureWatchlistedTitles: false,
      expectedRevision: 0,
    });

    expect(result.status).toBe("failed");
  });

  it("never sends a negative revision", () => {
    expect(preferencePath({ userId: 1, expectedRevision: -3 })).toBe(
      "/api/users/1/preferences?expected_revision=0",
    );
  });
});
