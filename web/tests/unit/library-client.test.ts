import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { createBffLibraryClient, libraryReadUrl } from "@/lib/library/client";

import { libraryResponse } from "./resource-fixtures";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

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
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify(libraryResponse), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-Request-ID": "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
        },
      }),
    ) as unknown as typeof fetch;

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

describe("the Library writes through the shared movie-state client", () => {
  it("exposes the same two write operations every other surface uses", () => {
    const client = createBffLibraryClient(
      vi.fn(async () => jsonResponse({})) as unknown as typeof fetch,
    );

    expect(typeof client.mutate).toBe("function");
    expect(typeof client.readState).toBe("function");
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
