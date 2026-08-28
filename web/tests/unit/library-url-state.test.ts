import { describe, expect, it } from "vitest";

import {
  DEFAULT_LIBRARY_USER_ID,
  LIBRARY_TABS,
  hasLibraryFilters,
  libraryHref,
  libraryUrlQuery,
  libraryViewKey,
  nextLibraryUrlState,
  normalizeSort,
  parseLibraryUrlState,
  parseLibraryYear,
  sortsForTab,
  type LibrarySort,
  type LibraryUrlState,
} from "@/lib/library/url-state";

function state(overrides: Partial<LibraryUrlState> = {}): LibraryUrlState {
  return {
    userId: DEFAULT_LIBRARY_USER_ID,
    tab: "rated",
    sort: "recent",
    query: "",
    genre: null,
    yearFrom: null,
    yearTo: null,
    cursor: null,
    ...overrides,
  };
}

function roundTrip(value: LibraryUrlState): LibraryUrlState {
  return parseLibraryUrlState(
    Object.fromEntries(libraryUrlQuery(value).entries()),
  );
}

describe("library URL state", () => {
  it("round-trips every tab, sort, query, and cursor combination", () => {
    for (const tab of LIBRARY_TABS) {
      for (const sort of sortsForTab(tab)) {
        for (const query of ["", "murder"]) {
          for (const cursor of [null, "opaque-cursor-2"]) {
            expect(roundTrip(state({ userId: 900000104, tab, sort, query, cursor }))).toEqual(
              state({ userId: 900000104, tab, sort, query, cursor }),
            );
          }
        }
      }
    }
  });

  it("round-trips the genre and year bounds the Seen tab adds", () => {
    const filtered = state({
      tab: "history",
      sort: "tmdb",
      genre: "Sci-Fi",
      yearFrom: 1990,
      yearTo: 1999,
    });

    expect(roundTrip(filtered)).toEqual(filtered);
    expect(libraryHref(filtered)).toBe(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&tab=history&sort=tmdb&genre=Sci-Fi&year_from=1990&year_to=1999`,
    );
  });

  it("clamps a year to the window the endpoint accepts, and drops anything else", () => {
    expect(parseLibraryYear("1990")).toBe(1990);
    expect(parseLibraryYear("1877")).toBeNull();
    expect(parseLibraryYear("2101")).toBeNull();
    expect(parseLibraryYear("19x0")).toBeNull();
    expect(parseLibraryYear("")).toBeNull();

    const parsed = parseLibraryUrlState({ year_from: "1200", year_to: "2019" });
    expect(parsed.yearFrom).toBeNull();
    expect(parsed.yearTo).toBe(2019);
  });

  it("drops both bounds when the range is inverted, as the endpoint would 422", () => {
    const parsed = parseLibraryUrlState({
      tab: "history",
      year_from: "2010",
      year_to: "1999",
    });
    expect(parsed.yearFrom).toBeNull();
    expect(parsed.yearTo).toBeNull();

    const edited = nextLibraryUrlState(state({ tab: "history", yearTo: 1999 }), {
      yearFrom: 2010,
    });
    expect(edited.yearFrom).toBeNull();
    expect(edited.yearTo).toBeNull();
  });

  it("writes only what differs from the defaults", () => {
    expect(libraryUrlQuery(state()).toString()).toBe(
      `userId=${DEFAULT_LIBRARY_USER_ID}`,
    );
    expect(
      libraryHref(state({ userId: 900000104, tab: "history", sort: "title", query: "fall" })),
    ).toBe("/library?userId=900000104&tab=history&sort=title&q=fall");
  });

  it("writes preview knobs and an alternate base path when asked", () => {
    const href = libraryHref(state({ tab: "watchlist" }), "/ui-preview/library", {
      fail: "library",
      empty: "",
    });

    expect(href).toBe(
      `/ui-preview/library?userId=${DEFAULT_LIBRARY_USER_ID}&tab=watchlist&fail=library`,
    );
  });

  it("falls back rather than trusting an unknown tab, sort, or user", () => {
    expect(
      parseLibraryUrlState({
        userId: "not-a-user",
        tab: "archive",
        sort: "sideways",
        q: "  spaced  ",
        genre: "   ",
      }),
    ).toEqual(state({ query: "spaced" }));
  });

  it("collapses whitespace so one search is one view", () => {
    // The endpoint normalizes before it fingerprints the query, and the cursor
    // is bound to that fingerprint — two spellings of one search must not
    // produce two incompatible page positions.
    expect(parseLibraryUrlState({ q: "  the   thing " }).query).toBe("the thing");
    expect(libraryViewKey(state({ query: "the thing" }))).toBe(
      libraryViewKey(parseLibraryUrlState({ q: "the   thing" })),
    );
  });

  it("offers each collection only the orderings it can answer", () => {
    expect(sortsForTab("rated")).toEqual(["recent", "title", "rating"]);
    expect(sortsForTab("watchlist")).toEqual(["recent", "title"]);
    expect(sortsForTab("history")).toEqual([
      "recent",
      "title",
      "rating",
      "release",
      "tmdb",
    ]);

    // A hand-edited sort a tab does not offer lands on Most recent rather than
    // on an error the reader cannot act on.
    expect(normalizeSort("rated", "tmdb" as LibrarySort)).toBe("recent");
    expect(normalizeSort("watchlist", "rating" as LibrarySort)).toBe("recent");
    expect(normalizeSort("history", "tmdb" as LibrarySort)).toBe("tmdb");

    expect(
      nextLibraryUrlState(state({ sort: "rating" }), { tab: "watchlist" }).sort,
    ).toBe("recent");
  });

  it("drops a cursor whenever the view it was issued for changes", () => {
    const paged = state({ tab: "history", cursor: "opaque-2" });

    expect(nextLibraryUrlState(paged, { tab: "rated" }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { sort: "title" }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { query: "burning" }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { genre: "Drama" }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { yearFrom: 1990 }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { yearTo: 1999 }).cursor).toBeNull();
    // Clearing a filter is a change like any other, and it has to be heard:
    // "All genres" and "no year" both arrive as an explicit null.
    expect(nextLibraryUrlState(paged, { genre: null }).genre).toBeNull();
    expect(
      nextLibraryUrlState(state({ tab: "history", genre: "Drama", yearTo: 1999 }), {
        genre: null,
        yearTo: null,
      }),
    ).toMatchObject({ genre: null, yearTo: null });
    // Re-selecting the same view is not a change, so the page position holds.
    expect(nextLibraryUrlState(paged, { tab: "history" }).cursor).toBe("opaque-2");
    // And asking for the top of an unchanged view is honoured rather than read
    // as "no opinion about the cursor".
    expect(nextLibraryUrlState(paged, { cursor: null }).cursor).toBeNull();
  });

  it("identifies a view precisely enough to tell a reload from a move", () => {
    const base = state();

    expect(libraryViewKey(base)).toBe(libraryViewKey({ ...base }));
    for (const moved of [
      { cursor: "next" },
      { query: "x" },
      { userId: 2 },
      { tab: "history" as const },
      { sort: "title" as const },
      { genre: "Drama" },
      { yearFrom: 1990 },
      { yearTo: 1999 },
    ]) {
      expect(libraryViewKey(base)).not.toBe(libraryViewKey({ ...base, ...moved }));
    }
  });

  it("keeps two views apart when a filter value contains the field separator", () => {
    // `q` is free text and the endpoint takes any genre through a deep link,
    // so a key joined on a separator that can appear inside a field lets these
    // two different views share one. The collision is silent and reads as data
    // corruption: nothing looks moved, the fetch is skipped, and the previous
    // query's rows stay on screen under the new query's URL.
    expect(
      libraryViewKey(state({ query: "The", genre: "(no genres listed)" })),
    ).not.toBe(libraryViewKey(state({ query: "The (no", genre: "genres listed)" })));
    // The same shape one field over, where a year bound meets a genre.
    expect(libraryViewKey(state({ genre: "Drama 1990" }))).not.toBe(
      libraryViewKey(state({ genre: "Drama", yearFrom: 1990 })),
    );
  });

  it("knows when the collection has been narrowed", () => {
    expect(hasLibraryFilters(state())).toBe(false);
    expect(hasLibraryFilters(state({ query: "blade" }))).toBe(true);
    expect(hasLibraryFilters(state({ genre: "Drama" }))).toBe(true);
    expect(hasLibraryFilters(state({ yearTo: 1999 }))).toBe(true);
    // A sort reorders the same set; it narrows nothing.
    expect(hasLibraryFilters(state({ tab: "history", sort: "tmdb" }))).toBe(false);
  });
});
