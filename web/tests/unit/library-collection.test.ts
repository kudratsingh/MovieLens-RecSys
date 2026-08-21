import { describe, expect, it } from "vitest";

import type { LibraryMovie, LibraryResponse, MovieState } from "@/lib/api";
import {
  actionRequest,
  affectsTasteSummary,
  appendLibraryPage,
  applyOptimisticState,
  movieMatchesTab,
  mutationAnnouncement,
  replaceMovieState,
  stateSummary,
  titleInitials,
} from "@/lib/library/collection";

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

describe("optimistic transitions follow the accepted feedback contract", () => {
  it("keeps the original watched time when a rating is edited", () => {
    const edited = applyOptimisticState(state({ rating: 3 }), { kind: "rate", rating: 5 }, NOW);

    expect(edited.rating).toBe(5);
    expect(edited.watched_at).toBe(WATCHED_AT);
    expect(edited.rating_updated_at).toBe(NOW);
  });

  it("marks an unwatched movie watched when it is rated, and clears the watchlist", () => {
    const rated = applyOptimisticState(
      state({ rating: null, rating_updated_at: null, watched_at: null, watchlisted_at: NOW }),
      { kind: "rate", rating: 4 },
      NOW,
    );

    expect(rated.watched_at).toBe(NOW);
    expect(rated.watchlisted_at).toBeNull();
  });

  it("leaves a movie watched when only its rating is deleted", () => {
    const cleared = applyOptimisticState(state(), { kind: "clear-rating" }, NOW);

    expect(cleared.rating).toBeNull();
    expect(cleared.rating_updated_at).toBeNull();
    expect(cleared.watched_at).toBe(WATCHED_AT);
    expect(movieMatchesTab(cleared, "history")).toBe(true);
    expect(movieMatchesTab(cleared, "rated")).toBe(false);
  });

  it("removes the positive interaction and the rating when history is removed", () => {
    const removed = applyOptimisticState(state(), { kind: "remove-history" }, NOW);

    expect(removed.watched_at).toBeNull();
    expect(removed.rating).toBeNull();
    expect(movieMatchesTab(removed, "history")).toBe(false);
  });

  it("treats watchlist and dismissal as independent of watched history", () => {
    const saved = applyOptimisticState(
      state({ watched_at: null, rating: null, rating_updated_at: null }),
      { kind: "save" },
      NOW,
    );
    expect(saved.watchlisted_at).toBe(NOW);
    expect(saved.watched_at).toBeNull();

    const dismissed = applyOptimisticState(saved, { kind: "dismiss" }, NOW);
    expect(dismissed.dismissed_at).toBe(NOW);
    expect(dismissed.watchlisted_at).toBeNull();
    expect(dismissed.rating).toBeNull();

    expect(applyOptimisticState(dismissed, { kind: "undismiss" }, NOW).dismissed_at).toBeNull();
  });

  it("never invents a revision the API has not issued", () => {
    for (const kind of ["rate", "clear-rating", "mark-watched", "remove-history"] as const) {
      expect(applyOptimisticState(state(), { kind, rating: 4 }, NOW).revision).toBe(7);
    }
  });

  it("maps each action to the resource and method the API publishes", () => {
    expect(actionRequest("rate")).toEqual({ resource: "rating", method: "PUT" });
    expect(actionRequest("clear-rating")).toEqual({ resource: "rating", method: "DELETE" });
    expect(actionRequest("remove-history")).toEqual({ resource: "watched", method: "DELETE" });
    expect(actionRequest("mark-watched")).toEqual({ resource: "watched", method: "PUT" });
    expect(actionRequest("save")).toEqual({ resource: "watchlist", method: "PUT" });
    expect(actionRequest("dismiss")).toEqual({ resource: "dismissal", method: "PUT" });
  });

  it("re-reads the ratings summary only for the actions that can change it", () => {
    expect(affectsTasteSummary("rate")).toBe(true);
    expect(affectsTasteSummary("clear-rating")).toBe(true);
    expect(affectsTasteSummary("remove-history")).toBe(true);
    expect(affectsTasteSummary("save")).toBe(false);
    expect(affectsTasteSummary("dismiss")).toBe(false);
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

  it("names the persona and the model meaning in mutation feedback", () => {
    expect(mutationAnnouncement("rate", "Burning", "Action Fan")).toBe(
      "Rating saved for Burning in Action Fan's library. It stays watched history; the star value is display feedback only.",
    );
    expect(mutationAnnouncement("clear-rating", "Burning", "Action Fan")).toContain(
      "still watched history",
    );
    expect(mutationAnnouncement("save", "Burning", "Action Fan")).toContain(
      "does not change recommendations",
    );
  });

  it("derives a stable initial pair because the payload carries no poster", () => {
    expect(titleInitials("The Handmaiden")).toBe("H");
    expect(titleInitials("Memories of Murder")).toBe("MM");
    expect(titleInitials("Action Fan")).toBe("AF");
    expect(titleInitials("")).toBe("?");
  });
});
