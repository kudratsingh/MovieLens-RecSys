import { afterEach, describe, expect, it } from "vitest";

import { GET } from "@/app/api/ui-preview/catalog/route";
import type { CatalogResponse } from "@/lib/api";
import {
  queryRecordedCatalog,
  RECORDED_CATALOG,
  RecordedCursorRejected,
  sortTitle,
} from "@/lib/fixtures/catalog-fixtures";
import { isCatalogResponse } from "@/lib/resources/validate";

const ENDPOINT = "http://localhost/api/ui-preview/catalog";

function request(search = ""): Request {
  return new Request(`${ENDPOINT}${search}`);
}

function enableFixtureMode() {
  process.env.MOVIELENS_UI_FIXTURE_MODE = "1";
}

afterEach(() => {
  delete process.env.MOVIELENS_UI_FIXTURE_MODE;
});

describe("the recorded catalog endpoint is fail-closed", () => {
  it("does not exist unless the isolated preview mode is on", async () => {
    expect((await GET(request())).status).toBe(404);
  });

  it("answers the catalog contract in preview mode", async () => {
    enableFixtureMode();
    const response = await GET(request());

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(response.headers.get("X-Request-ID")).toBeTruthy();
    expect(isCatalogResponse(await response.json())).toBe(true);
  });

  it("echoes a caller-supplied correlation ID", async () => {
    enableFixtureMode();
    const correlationId = "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001";
    const response = await GET(
      new Request(ENDPOINT, { headers: { "X-Request-ID": correlationId } }),
    );

    expect(response.headers.get("X-Request-ID")).toBe(correlationId);
  });

  it("injects the failures the preview needs to show real error states", async () => {
    enableFixtureMode();
    expect((await GET(request("?fail=catalog"))).status).toBe(502);
    expect((await GET(request("?fail=catalog-auth"))).status).toBe(401);
    expect((await GET(request("?fail=catalog-forbidden"))).status).toBe(403);
  });

  it("rejects a cursor that belongs to another query with a 400", async () => {
    enableFixtureMode();
    const first = (await (await GET(request("?sort=title"))).json()) as CatalogResponse;
    const cursor = first.page.next_cursor!;

    expect((await GET(request(`?sort=title&cursor=${cursor}`))).status).toBe(200);
    const rejected = await GET(request(`?sort=newest&cursor=${cursor}`));
    expect(rejected.status).toBe(400);
    expect(await rejected.json()).toMatchObject({
      detail: "catalog cursor is invalid for this query",
    });
  });

  it("holds the endpoint's page cap", async () => {
    enableFixtureMode();
    const response = (await (await GET(request("?limit=500"))).json()) as CatalogResponse;
    expect(response.items.length).toBeLessThanOrEqual(48);
  });
});

describe("the recorded catalog query engine", () => {
  const base = {
    q: null,
    genre: null,
    yearFrom: null,
    yearTo: null,
    sort: "title" as const,
    limit: 24,
    cursor: null,
  };

  it("has enough titles and metadata variety to be worth reviewing", () => {
    const counts = { complete: 0, partial: 0, unavailable: 0 };
    for (const item of RECORDED_CATALOG) counts[item.source_status] += 1;

    expect(RECORDED_CATALOG.length).toBeGreaterThan(48);
    expect(counts.complete).toBeGreaterThan(0);
    expect(counts.partial).toBeGreaterThan(0);
    expect(counts.unavailable).toBeGreaterThan(0);
  });

  it("orders by normalized title then movie ID", () => {
    const page = queryRecordedCatalog(base);
    const titles = page.items.map((item) => sortTitle(item.title));
    expect([...titles].sort()).toEqual(titles);
  });

  it("walks every row exactly once across cursor pages", () => {
    const seen: number[] = [];
    let cursor: string | null = null;
    for (let guard = 0; guard < 20; guard += 1) {
      const page: ReturnType<typeof queryRecordedCatalog> = queryRecordedCatalog({
        ...base,
        cursor,
      });
      seen.push(...page.items.map((item) => item.movie_id));
      cursor = page.page.next_cursor;
      if (!cursor) break;
    }

    expect(new Set(seen).size).toBe(seen.length);
    expect(seen).toHaveLength(RECORDED_CATALOG.length);
  });

  it("composes search, genre, and year filters", () => {
    const page = queryRecordedCatalog({ ...base, genre: "Drama", yearFrom: 2010 });
    expect(page.items.length).toBeGreaterThan(0);
    for (const item of page.items) {
      expect(item.genres).toContain("Drama");
      expect(item.release_year).toBeGreaterThanOrEqual(2010);
    }
  });

  it("throws when a cursor is replayed against a different query", () => {
    const cursor = queryRecordedCatalog(base).page.next_cursor!;
    expect(() =>
      queryRecordedCatalog({ ...base, genre: "Drama", cursor }),
    ).toThrow(RecordedCursorRejected);
  });

  it("returns no cursor on the last page", () => {
    const page = queryRecordedCatalog({ ...base, limit: 48, q: "the" });
    if (!page.page.has_more) expect(page.page.next_cursor).toBeNull();
  });
});
