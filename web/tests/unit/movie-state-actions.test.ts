import { describe, expect, it } from "vitest";

import type { MovieState } from "@/lib/api";
import {
  applyActionToDisplay,
  applyActionToState,
  displayState,
  movieStateActionOf,
  ratingAction,
  sameAction,
  toggleAction,
  UNKNOWN_MOVIE_STATE,
  type MovieStateAction,
} from "@/lib/movie-state/actions";
import { movieMatchesTab } from "@/lib/library/collection";

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

const RATE_4: MovieStateAction = { resource: "rating", method: "PUT", rating: 4 };
const CLEAR_RATING: MovieStateAction = { resource: "rating", method: "DELETE" };
const MARK_WATCHED: MovieStateAction = { resource: "watched", method: "PUT" };
const REMOVE_HISTORY: MovieStateAction = { resource: "watched", method: "DELETE" };
const SAVE: MovieStateAction = { resource: "watchlist", method: "PUT" };
const DISMISS: MovieStateAction = { resource: "dismissal", method: "PUT" };
const UNDISMISS: MovieStateAction = { resource: "dismissal", method: "DELETE" };

describe("the display projection", () => {
  it("derives display state from the canonical timestamps", () => {
    expect(
      displayState(
        state({ rating: 4, watched_at: WATCHED_AT, watchlisted_at: null }),
      ),
    ).toEqual({ watched: true, watchlisted: false, dismissed: false, rating: 4 });
  });

  it("stays honestly unknown when no record has been read", () => {
    expect(displayState(null)).toEqual(UNKNOWN_MOVIE_STATE);
    expect(UNKNOWN_MOVIE_STATE).toEqual({
      watched: false,
      watchlisted: false,
      dismissed: false,
      rating: null,
    });
  });

  it("sends the removing method when a toggle already holds its value", () => {
    const saved = { ...UNKNOWN_MOVIE_STATE, watchlisted: true, dismissed: true };
    expect(toggleAction("watchlist", saved)).toEqual({
      resource: "watchlist",
      method: "DELETE",
    });
    expect(toggleAction("dismissal", saved)).toEqual({
      resource: "dismissal",
      method: "DELETE",
    });
    expect(toggleAction("watched", saved)).toEqual({
      resource: "watched",
      method: "PUT",
    });
  });

  it("treats one intent as one intent, whatever object carries it", () => {
    expect(sameAction(RATE_4, { resource: "rating", method: "PUT", rating: 4 })).toBe(true);
    expect(sameAction(RATE_4, { resource: "rating", method: "PUT", rating: 5 })).toBe(false);
    expect(sameAction(RATE_4, CLEAR_RATING)).toBe(false);
    expect(sameAction(MARK_WATCHED, REMOVE_HISTORY)).toBe(false);
  });

  it("rebuilds the action a request describes", () => {
    expect(movieStateActionOf({ resource: "rating", method: "PUT", rating: 3 })).toEqual({
      resource: "rating",
      method: "PUT",
      rating: 3,
    });
    expect(movieStateActionOf({ resource: "watched", method: "DELETE" })).toEqual(
      REMOVE_HISTORY,
    );
    expect(ratingAction(null)).toEqual(CLEAR_RATING);
    expect(ratingAction(2)).toEqual({ resource: "rating", method: "PUT", rating: 2 });
  });
});

describe("optimistic transitions follow the accepted feedback contract", () => {
  it("keeps the original watched time when a rating is edited", () => {
    const edited = applyActionToState(
      state({ rating: 3 }),
      { resource: "rating", method: "PUT", rating: 5 },
      NOW,
    );

    expect(edited.rating).toBe(5);
    expect(edited.watched_at).toBe(WATCHED_AT);
    expect(edited.rating_updated_at).toBe(NOW);
  });

  it("marks an unwatched movie watched when it is rated, and clears the watchlist", () => {
    const rated = applyActionToState(
      state({
        rating: null,
        rating_updated_at: null,
        watched_at: null,
        watchlisted_at: NOW,
      }),
      RATE_4,
      NOW,
    );

    expect(rated.watched_at).toBe(NOW);
    expect(rated.watchlisted_at).toBeNull();
  });

  it("leaves a movie watched when only its rating is deleted", () => {
    const cleared = applyActionToState(state(), CLEAR_RATING, NOW);

    expect(cleared.rating).toBeNull();
    expect(cleared.rating_updated_at).toBeNull();
    expect(cleared.watched_at).toBe(WATCHED_AT);
    expect(movieMatchesTab(cleared, "history")).toBe(true);
    expect(movieMatchesTab(cleared, "rated")).toBe(false);
  });

  it("removes the positive interaction and the rating when history is removed", () => {
    const removed = applyActionToState(state(), REMOVE_HISTORY, NOW);

    expect(removed.watched_at).toBeNull();
    expect(removed.rating).toBeNull();
    expect(movieMatchesTab(removed, "history")).toBe(false);
  });

  it("clears the watchlist when a movie becomes watched", () => {
    const saved = applyActionToState(
      state({ watched_at: null, rating: null, rating_updated_at: null }),
      SAVE,
      NOW,
    );
    expect(saved.watchlisted_at).toBe(NOW);
    expect(saved.watched_at).toBeNull();

    expect(applyActionToState(saved, MARK_WATCHED, NOW).watchlisted_at).toBeNull();
  });

  it("treats dismissal as an exclusion that also drops the watchlist entry", () => {
    const saved = applyActionToState(
      state({ watched_at: null, rating: null, rating_updated_at: null }),
      SAVE,
      NOW,
    );
    const dismissed = applyActionToState(saved, DISMISS, NOW);

    expect(dismissed.dismissed_at).toBe(NOW);
    expect(dismissed.watchlisted_at).toBeNull();
    expect(dismissed.rating).toBeNull();
    expect(applyActionToState(dismissed, UNDISMISS, NOW).dismissed_at).toBeNull();
  });

  it("keeps watchlist organizational: it changes nothing else", () => {
    const watched = applyActionToState(state({ watchlisted_at: null }), SAVE, NOW);

    expect(watched.watched_at).toBe(WATCHED_AT);
    expect(watched.rating).toBe(4.5);
    expect(watched.dismissed_at).toBeNull();
  });

  it("never invents a revision the API has not issued", () => {
    for (const action of [RATE_4, CLEAR_RATING, MARK_WATCHED, REMOVE_HISTORY, DISMISS]) {
      expect(applyActionToState(state(), action, NOW).revision).toBe(7);
    }
  });
});

describe("the control projection mirrors the canonical one", () => {
  it("agrees with the record transition on every action", () => {
    const record = state({
      watched_at: null,
      rating: null,
      rating_updated_at: null,
      watchlisted_at: NOW,
    });

    for (const action of [RATE_4, CLEAR_RATING, MARK_WATCHED, REMOVE_HISTORY, SAVE, DISMISS]) {
      expect(applyActionToDisplay(displayState(record), action)).toEqual(
        displayState(applyActionToState(record, action, NOW)),
      );
    }
  });
});
