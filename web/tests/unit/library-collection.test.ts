import { describe, expect, it } from "vitest";

import type { LibraryMovie, LibraryResponse, MovieState } from "@/lib/api";
import {
  affectsTasteSummary,
  appendLibraryPage,
  movieMatchesTab,
  movieMetaLine,
  replaceMovieState,
  stateSummary,
} from "@/lib/library/collection";
import type { MovieStateAction } from "@/lib/movie-state/actions";

const WATCHED_AT = "2026-08-16T21:05:00Z";
const NOW = "2026-08-21T12:00:00Z";

function state(overrides: Partial<MovieState> = {}): MovieState {
  return {
    dismissed_at: null,
    movie_id: 103,
    rating: 4.5,
    rating_updated_at: WATCHED_AT,
    revision: 7,
    tenant_id: "demo",
    updated_at: WATCHED_AT,
    user_id: 900000101,
    watched_at: WATCHED_AT,
    watchlisted_at: null,
    ...overrides,
  };
}

function movie(movieId: number, overrides: Partial<MovieState> = {}): LibraryMovie {
  return {
    genres: ["Crime"],
    movie_id: movieId,
    poster_url: null,
    release_year: 2003,
    state: state({ movie_id: movieId, ...overrides }),
    title: `Movie ${movieId}`,
  };
}

function page(items: LibraryMovie[], nextCursor: string | null): LibraryResponse {
  return {
    counts: { history: 15, rated: 12, watchlist: 4 },
    items,
    page: { has_more: nextCursor !== null, next_cursor: nextCursor },
    query: null,
    sort: "recent",
    tab: "history",
    tenant_id: "demo",
    user_id: 900000101,
  };
}

describe("appending cursor pages", () => {
  it("appends without duplicating a row the next page repeats", () => {
    const first = page([movie(1), movie(2), movie(3)], "cursor-3");
    const second = page([movie(3), movie(4)], null);

    const merged = appendLibraryPage(first, second);

    expect(merged.items.map((item) => item.movie_id)).toEqual([1, 2, 3, 4]);
    expect(merged.page.has_more).toBe(false);
  });

  it("keeps a repeated row in place while taking its fresher state", () => {
    const first = page([movie(1), movie(2, { rating: 3 })], "cursor-2");
    const second = page([movie(2, { rating: 5 }), movie(9)], null);

    const merged = appendLibraryPage(first, second);

    expect(merged.items.map((item) => item.movie_id)).toEqual([1, 2, 9]);
    expect(merged.items[1].state.rating).toBe(5);
  });

  it("takes counts and page info from the newest response", () => {
    const first = page([movie(1)], "cursor-1");
    const second: LibraryResponse = {
      ...page([movie(2)], "cursor-2"),
      counts: { history: 20, rated: 14, watchlist: 5 },
    };

    const merged = appendLibraryPage(first, second);

    expect(merged.counts).toEqual({ history: 20, rated: 14, watchlist: 5 });
    expect(merged.page.next_cursor).toBe("cursor-2");
  });
});

describe("derived Library state", () => {
  it("re-reads the ratings summary only for the actions that can change it", () => {
    const affects: MovieStateAction[] = [
      { resource: "rating", method: "PUT", rating: 4 },
      { resource: "rating", method: "DELETE" },
      { resource: "watched", method: "DELETE" },
    ];
    const leaves: MovieStateAction[] = [
      { resource: "watched", method: "PUT" },
      { resource: "watchlist", method: "PUT" },
      { resource: "dismissal", method: "PUT" },
    ];

    for (const action of affects) expect(affectsTasteSummary(action)).toBe(true);
    for (const action of leaves) expect(affectsTasteSummary(action)).toBe(false);
  });

  it("knows which collection a movie still belongs in", () => {
    expect(movieMatchesTab(state(), "history")).toBe(true);
    expect(movieMatchesTab(state({ rating: null }), "rated")).toBe(false);
    expect(movieMatchesTab(state({ watchlisted_at: NOW }), "watchlist")).toBe(true);
  });
});

describe("reconciliation and row copy", () => {
  it("replaces only the movie the API answered about", () => {
    const collection = page([movie(1), movie(2)], null);
    const canonical = state({ movie_id: 2, rating: 1, revision: 9 });

    const reconciled = replaceMovieState(collection, 2, canonical);

    expect(reconciled.items[0].state.revision).toBe(7);
    expect(reconciled.items[1].state).toEqual(canonical);
  });

  it("spells state out in words rather than leaving it to colour", () => {
    expect(stateSummary(state(), "history")).toEqual([
      "Watched Aug 16, 2026",
      "Rated 4.5 of 5",
    ]);
    expect(stateSummary(state({ rating: null }), "history")).toEqual([
      "Watched Aug 16, 2026",
      "Not rated",
    ]);
    expect(
      stateSummary(
        state({ watched_at: null, rating: null, watchlisted_at: WATCHED_AT, dismissed_at: NOW }),
        "watchlist",
      ),
    ).toEqual(["Saved Aug 16, 2026", "Excluded from recommendations"]);
  });

  it("prints the year the title no longer carries, and names a missing genre", () => {
    expect(movieMetaLine(2003, ["Crime", "Mystery"])).toBe("2003 · Crime · Mystery");
    expect(movieMetaLine(null, ["Drama"])).toBe("Drama");
    expect(movieMetaLine(1995, [])).toBe("1995 · Genres unavailable");
    expect(movieMetaLine(null, [])).toBe("Genres unavailable");
  });
});
