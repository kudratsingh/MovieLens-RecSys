import { describe, expect, it } from "vitest";

import {
  DEFAULT_LIBRARY_USER_ID,
  LIBRARY_TABS,
  libraryHref,
  libraryUrlQuery,
  libraryViewKey,
  nextLibraryUrlState,
  normalizeSort,
  parseLibraryUrlState,
  sortsForTab,
  type LibrarySort,
  type LibraryUrlState,
} from "@/lib/library/url-state";

function roundTrip(state: LibraryUrlState): LibraryUrlState {
  return parseLibraryUrlState(
    Object.fromEntries(libraryUrlQuery(state).entries()),
  );
}

describe("library URL state", () => {
  it("round-trips every tab, sort, query, and cursor combination", () => {
    for (const tab of LIBRARY_TABS) {
      for (const sort of sortsForTab(tab)) {
        for (const query of ["", "murder"]) {
          for (const cursor of [null, "opaque-cursor-2"]) {
            const state: LibraryUrlState = {
              userId: 900000104,
              tab,
              sort,
              query,
              cursor,
            };
            expect(roundTrip(state)).toEqual(state);
          }
        }
      }
    }
  });

  it("writes only what differs from the defaults", () => {
    const resting = libraryUrlQuery({
      userId: DEFAULT_LIBRARY_USER_ID,
      tab: "rated",
      sort: "recent",
      query: "",
      cursor: null,
    });

    expect(resting.toString()).toBe(`userId=${DEFAULT_LIBRARY_USER_ID}`);
    expect(libraryHref({
      userId: 900000104,
      tab: "history",
      sort: "title",
      query: "fall",
      cursor: null,
    })).toBe("/library?userId=900000104&tab=history&sort=title&q=fall");
  });

  it("writes preview knobs and an alternate base path when asked", () => {
    const href = libraryHref(
      { userId: 900000101, tab: "watchlist", sort: "recent", query: "", cursor: null },
      "/ui-preview/library",
      { fail: "library", empty: "" },
    );

    expect(href).toBe(
      "/ui-preview/library?userId=900000101&tab=watchlist&fail=library",
    );
  });

  it("falls back rather than trusting an unknown tab, sort, or user", () => {
    const state = parseLibraryUrlState({
      userId: "not-a-user",
      tab: "archive",
      sort: "sideways",
      q: "  spaced  ",
    });

    expect(state).toEqual({
      userId: DEFAULT_LIBRARY_USER_ID,
      tab: "rated",
      sort: "recent",
      query: "spaced",
      cursor: null,
    });
  });

  it("keeps rating sort to the one collection where every row has a rating", () => {
    expect(sortsForTab("rated")).toContain("rating");
    expect(sortsForTab("watchlist")).not.toContain("rating");
    expect(normalizeSort("history", "rating" as LibrarySort)).toBe("recent");

    const moved = nextLibraryUrlState(
      { userId: 1, tab: "rated", sort: "rating", query: "", cursor: null },
      { tab: "watchlist" },
    );
    expect(moved.sort).toBe("recent");
  });

  it("drops a cursor whenever the view it was issued for changes", () => {
    const paged: LibraryUrlState = {
      userId: 1,
      tab: "history",
      sort: "recent",
      query: "",
      cursor: "opaque-2",
    };

    expect(nextLibraryUrlState(paged, { tab: "rated" }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { sort: "title" }).cursor).toBeNull();
    expect(nextLibraryUrlState(paged, { query: "burning" }).cursor).toBeNull();
    // Re-selecting the same view is not a change, so the page position holds.
    expect(nextLibraryUrlState(paged, { tab: "history" }).cursor).toBe("opaque-2");
  });

  it("identifies a view precisely enough to tell a reload from a move", () => {
    const base: LibraryUrlState = {
      userId: 1,
      tab: "rated",
      sort: "recent",
      query: "",
      cursor: null,
    };

    expect(libraryViewKey(base)).toBe(libraryViewKey({ ...base }));
    expect(libraryViewKey(base)).not.toBe(libraryViewKey({ ...base, cursor: "next" }));
    expect(libraryViewKey(base)).not.toBe(libraryViewKey({ ...base, query: "x" }));
    expect(libraryViewKey(base)).not.toBe(libraryViewKey({ ...base, userId: 2 }));
  });
});
