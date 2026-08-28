/**
 * The four movie states, the transitions between them, and how they are shown.
 *
 * Every surface that can change a movie's state — Discover, Browse, movie
 * detail, Library — used to carry its own copy of this table, and the copies
 * had already drifted: one of them left a watchlist entry standing after a
 * movie was marked watched, which is not what the API commits. There is one
 * table now, and it is the one in ADR 0012 §"State transitions" and
 * `docs/frontend/library-feedback-contract.md`:
 *
 * - watched preserves the first watched time and clears watchlist;
 * - a rating implies watched, so it does the same;
 * - deleting a rating leaves the movie watched;
 * - removing history takes the rating with it;
 * - watchlist is organizational and changes nothing else;
 * - dismissal is an undoable exclusion that also clears watchlist.
 *
 * Nothing here invents a `revision`. The projections below are what a control
 * shows between the click and the committed response; the value the code
 * believes only ever comes from the API.
 */

import type { MovieState } from "@/lib/api";

export type MovieStateResource = "watched" | "rating" | "watchlist" | "dismissal";

export type MovieStateAction =
  | { resource: "watchlist"; method: "PUT" | "DELETE" }
  | { resource: "watched"; method: "PUT" | "DELETE" }
  | { resource: "dismissal"; method: "PUT" | "DELETE" }
  | { resource: "rating"; method: "PUT"; rating: number }
  | { resource: "rating"; method: "DELETE" };

/** What a control renders: booleans and a star value, no timestamps. */
export type MovieDisplayState = {
  watched: boolean;
  watchlisted: boolean;
  dismissed: boolean;
  rating: number | null;
};

/**
 * A recommendation response carries no per-item state, so a card that has not
 * been told otherwise is honestly unknown rather than "not saved".
 */
export const UNKNOWN_MOVIE_STATE: MovieDisplayState = {
  watched: false,
  watchlisted: false,
  dismissed: false,
  rating: null,
};

export function displayState(
  state: MovieState | null | undefined,
): MovieDisplayState {
  if (!state) return UNKNOWN_MOVIE_STATE;
  return {
    watched: state.watched_at !== null,
    watchlisted: state.watchlisted_at !== null,
    dismissed: state.dismissed_at !== null,
    rating: state.rating,
  };
}

/** The write a toggle button should send, given what the control is showing. */
export function toggleAction(
  resource: Exclude<MovieStateResource, "rating">,
  current: MovieDisplayState,
): MovieStateAction {
  const held =
    resource === "watchlist"
      ? current.watchlisted
      : resource === "watched"
        ? current.watched
        : current.dismissed;
  return { resource, method: held ? "DELETE" : "PUT" };
}

/** Rebuilds the action a request describes, for anything that replays one. */
export function movieStateActionOf(request: {
  resource: MovieStateResource;
  method: "PUT" | "DELETE";
  rating?: number;
}): MovieStateAction {
  if (request.resource === "rating") {
    return request.method === "PUT"
      ? { resource: "rating", method: "PUT", rating: request.rating ?? 0 }
      : { resource: "rating", method: "DELETE" };
  }
  return { resource: request.resource, method: request.method };
}

export function ratingAction(value: number | null): MovieStateAction {
  return value === null
    ? { resource: "rating", method: "DELETE" }
    : { resource: "rating", method: "PUT", rating: value };
}

/**
 * Which way a decision travels.
 *
 * Quick Picks taught this vocabulary first — a swipe right saves, a swipe up
 * marks watched, a swipe left dismisses — and Discover's featured slot now
 * moves the same way when it advances. Keeping the map here rather than in
 * either surface is what makes that one gesture language instead of two that
 * happen to agree today: `resolveSwipe` classifies gestures against it and the
 * Discover advance animates along it.
 */
export type DecisionDirection = "left" | "right" | "up";

export const DECISION_DIRECTION: Record<MovieStateResource, DecisionDirection> = {
  watchlist: "right",
  // A rating implies watched, so it travels the way watched does.
  watched: "up",
  rating: "up",
  dismissal: "left",
};

export function decisionDirection(action: MovieStateAction): DecisionDirection {
  return DECISION_DIRECTION[action.resource];
}

/** Two actions are the same intent when they would produce the same request. */
export function sameAction(left: MovieStateAction, right: MovieStateAction): boolean {
  if (left.resource !== right.resource || left.method !== right.method) return false;
  const leftRating = left.resource === "rating" && left.method === "PUT" ? left.rating : null;
  const rightRating =
    right.resource === "rating" && right.method === "PUT" ? right.rating : null;
  return leftRating === rightRating;
}

/** The optimistic frame for a control, derived from the same table. */
export function applyActionToDisplay(
  current: MovieDisplayState,
  action: MovieStateAction,
): MovieDisplayState {
  switch (action.resource) {
    case "watchlist":
      return { ...current, watchlisted: action.method === "PUT" };
    case "watched":
      return action.method === "PUT"
        ? { ...current, watched: true, watchlisted: false }
        : { ...current, watched: false, rating: null };
    case "rating":
      return action.method === "PUT"
        ? { ...current, watched: true, watchlisted: false, rating: action.rating }
        : { ...current, rating: null };
    case "dismissal":
      return action.method === "PUT"
        ? { ...current, dismissed: true, watchlisted: false }
        : { ...current, dismissed: false };
  }
}

/**
 * The same transition against a canonical record, for surfaces that project a
 * whole collection rather than one control. `revision` is deliberately left
 * alone: it is the server's optimistic-locking token, and inventing one here
 * would send a value the backend never issued.
 */
export function applyActionToState(
  state: MovieState,
  action: MovieStateAction,
  now: string,
): MovieState {
  switch (action.resource) {
    case "watchlist":
      return {
        ...state,
        watchlisted_at: action.method === "PUT" ? (state.watchlisted_at ?? now) : null,
      };
    case "watched":
      return action.method === "PUT"
        ? { ...state, watched_at: state.watched_at ?? now, watchlisted_at: null }
        : { ...state, watched_at: null, rating: null, rating_updated_at: null };
    case "rating":
      return action.method === "PUT"
        ? {
            ...state,
            rating: action.rating,
            rating_updated_at: now,
            // Rating implies watched, and the first watched time is preserved.
            watched_at: state.watched_at ?? now,
            watchlisted_at: null,
          }
        : { ...state, rating: null, rating_updated_at: null };
    case "dismissal":
      return action.method === "PUT"
        ? { ...state, dismissed_at: now, watchlisted_at: null }
        : { ...state, dismissed_at: null };
  }
}
