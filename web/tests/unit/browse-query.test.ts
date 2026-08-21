import { describe, expect, it } from "vitest";

import {
  browseFilterKey,
  browseHref,
  browseSearchParams,
  catalogRequestParams,
  hasActiveBrowseFilters,
  parseBrowseQuery,
  withBrowseFilters,
  type BrowseQuery,
} from "@/lib/browse/query";

const parse = (search: string) => parseBrowseQuery(new URLSearchParams(search));

describe("browse query parsing", () => {
  it("falls back to the default cut when nothing is asked for", () => {
    expect(parse("")).toEqual({
      q: "",
      genre: null,
      yearFrom: null,
      yearTo: null,
      sort: "title",
      cursor: null,
    });
  });

  it("normalizes a search so one query has one spelling", () => {
    expect(parse("q=++the%20%20handmaiden+").q).toBe("the handmaiden");
    expect(parse(`q=${"x".repeat(200)}`).q).toHaveLength(120);
  });

  it("drops year bounds the endpoint would reject", () => {
    expect(parse("year_from=1700").yearFrom).toBeNull();
    expect(parse("year_from=abcd").yearFrom).toBeNull();
    expect(parse("year_from=1990&year_to=2000")).toMatchObject({
      yearFrom: 1990,
      yearTo: 2000,
    });
  });

  it("treats an inverted range as no range rather than guessing a bound", () => {
    expect(parse("year_from=2010&year_to=1990")).toMatchObject({
      yearFrom: null,
      yearTo: null,
    });
  });

  it("only accepts sorts the endpoint implements", () => {
    expect(parse("sort=popular").sort).toBe("popular");
    expect(parse("sort=whatever").sort).toBe("title");
  });

  it("ignores a cursor longer than the endpoint accepts", () => {
    expect(parse(`cursor=${"c".repeat(1025)}`).cursor).toBeNull();
    expect(parse("cursor=abc").cursor).toBe("abc");
  });
});

describe("browse query serialization", () => {
  it("round-trips every field through the URL", () => {
    const query: BrowseQuery = {
      q: "burning",
      genre: "Drama",
      yearFrom: 2010,
      yearTo: 2019,
      sort: "newest",
      cursor: "opaque",
    };

    expect(parseBrowseQuery(browseSearchParams(query))).toEqual(query);
  });

  it("omits defaults so the same cut always produces the same address", () => {
    const cleared = parse("");
    expect(browseSearchParams(cleared).toString()).toBe("");
    expect(browseHref("/browse", cleared)).toBe("/browse");
    expect(browseHref("/browse", { ...cleared, genre: "Drama" })).toBe(
      "/browse?genre=Drama",
    );
  });

  it("keeps route parameters that are not filters, such as the persona", () => {
    const query = parse("q=burning");
    expect(browseHref("/browse", query, { user: "900000104" })).toBe(
      "/browse?user=900000104&q=burning",
    );
    // A cleared cut still has to name the persona it is clearing for.
    expect(
      browseHref("/browse", parse(""), { user: "900000104" }),
    ).toBe("/browse?user=900000104");
    // The catalog query wins on a collision; nothing else may forge a filter.
    expect(browseHref("/browse", query, { q: "forged" })).toBe("/browse?q=burning");
  });

  it("keys the filter set without the cursor so paging does not restart it", () => {
    const first = parse("q=burning&sort=newest");
    const paged = parse("q=burning&sort=newest&cursor=opaque");

    expect(browseFilterKey(first)).toBe(browseFilterKey(paged));
    expect(browseFilterKey(parse("q=burning"))).not.toBe(browseFilterKey(first));
  });

  it("reports an active cut for any non-default field", () => {
    expect(hasActiveBrowseFilters(parse(""))).toBe(false);
    expect(hasActiveBrowseFilters(parse("cursor=opaque"))).toBe(false);
    expect(hasActiveBrowseFilters(parse("sort=popular"))).toBe(true);
    expect(hasActiveBrowseFilters(parse("genre=Drama"))).toBe(true);
  });
});

describe("filter edits invalidate the cursor", () => {
  const paged = parse("q=burning&genre=Drama&sort=newest&cursor=opaque");

  it("drops the cursor on every filter change", () => {
    for (const patch of [
      { q: "other" },
      { genre: null },
      { sort: "popular" as const },
      { yearFrom: 1990, yearTo: 1999 },
    ]) {
      expect(withBrowseFilters(paged, patch).cursor).toBeNull();
    }
  });

  it("drops it even when the edit leaves the filters identical", () => {
    // The endpoint binds a cursor to a query fingerprint, not to the values a
    // control happens to hold, so "no visible change" is not a safe exception.
    expect(withBrowseFilters(paged, { q: paged.q }).cursor).toBeNull();
  });

  it("keeps an edited range valid", () => {
    expect(withBrowseFilters(paged, { yearFrom: 2020, yearTo: 1990 })).toMatchObject({
      yearFrom: null,
      yearTo: null,
    });
  });
});

describe("catalog request parameters", () => {
  it("always pins the sort and page size the endpoint should use", () => {
    const params = catalogRequestParams(parse("q=burning&genre=Drama"));
    expect(params.get("q")).toBe("burning");
    expect(params.get("genre")).toBe("Drama");
    expect(params.get("sort")).toBe("title");
    expect(params.get("limit")).toBe("24");
    expect(params.get("cursor")).toBeNull();
  });

  it("never asks for more than the endpoint's hard page cap", () => {
    expect(catalogRequestParams(parse(""), { limit: 500 }).get("limit")).toBe("48");
    expect(catalogRequestParams(parse(""), { limit: 0 }).get("limit")).toBe("1");
  });

  it("carries a cursor only when one is supplied for this request", () => {
    const query = parse("cursor=from-url");
    expect(catalogRequestParams(query).get("cursor")).toBeNull();
    expect(catalogRequestParams(query, { cursor: "next" }).get("cursor")).toBe("next");
  });
});
