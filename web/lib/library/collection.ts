/**
 * Pure collection logic for the Library route.
 *
 * The component that uses this is busy with focus, announcements, and network
 * state, so the parts that are easy to get subtly wrong — page appends, the
 * optimistic projection of a movie-state transition, and the copy that
 * describes what a transition actually means to the model — live here where
 * they can be tested directly.
 *
 * Every transition below mirrors the accepted contract in ADR 0012 and
 * `docs/frontend/library-feedback-contract.md`. Watched is the positive
 * interaction; star magnitude is display feedback; deleting a rating leaves the
 * movie watched; removing history removes the positive interaction; watchlist
 * is organizational only; dismissal is an undoable exclusion rather than a
 * learned negative.
 */

import type { LibraryMovie, LibraryResponse, MovieState } from "@/lib/api";
import type { LibraryTab } from "@/lib/library/url-state";

export type LibraryActionKind =
  | "rate"
  | "clear-rating"
  | "mark-watched"
  | "remove-history"
  | "save"
  | "unsave"
  | "dismiss"
  | "undismiss";

export type LibraryAction = {
  kind: LibraryActionKind;
  /** Half-star value, required for `rate` and ignored otherwise. */
  rating?: number;
};

export type FeedbackResource = "watched" | "rating" | "watchlist" | "dismissal";

const ACTION_REQUESTS: Record<
  LibraryActionKind,
  { resource: FeedbackResource; method: "PUT" | "DELETE" }
> = {
  rate: { resource: "rating", method: "PUT" },
  "clear-rating": { resource: "rating", method: "DELETE" },
  "mark-watched": { resource: "watched", method: "PUT" },
  "remove-history": { resource: "watched", method: "DELETE" },
  save: { resource: "watchlist", method: "PUT" },
  unsave: { resource: "watchlist", method: "DELETE" },
  dismiss: { resource: "dismissal", method: "PUT" },
  undismiss: { resource: "dismissal", method: "DELETE" },
};

export function actionRequest(kind: LibraryActionKind) {
  return ACTION_REQUESTS[kind];
}

/** Only these actions can change the rating-derived taste summary. */
export function affectsTasteSummary(kind: LibraryActionKind): boolean {
  return kind === "rate" || kind === "clear-rating" || kind === "remove-history";
}

/**
 * Appends a cursor page to the page already on screen.
 *
 * Cursor pagination can legitimately re-deliver a row — a row that moved under
 * a concurrent write, or a boundary tie — so identity is what decides, not
 * position. The later copy of a movie wins because it is the fresher read of
 * that movie's state, while the original position is kept so the list does not
 * reshuffle under the reader.
 */
export function appendLibraryPage(
  current: LibraryResponse,
  next: LibraryResponse,
): LibraryResponse {
  const incoming = new Map(next.items.map((movie) => [movie.movie_id, movie]));
  const merged: LibraryMovie[] = current.items.map(
    (movie) => incoming.get(movie.movie_id) ?? movie,
  );
  const seen = new Set(current.items.map((movie) => movie.movie_id));
  for (const movie of next.items) {
    if (!seen.has(movie.movie_id)) merged.push(movie);
  }
  // Counts, page, and echoed query come from the newest response; the items are
  // the accumulation of every page read so far.
  return { ...next, items: merged };
}

/**
 * Projects a transition onto a movie's state for the moment between the click
 * and the committed response.
 *
 * `revision` is deliberately left alone: it is the server's optimistic-locking
 * token, and inventing one here would send a value the backend never issued.
 */
export function applyOptimisticState(
  state: MovieState,
  action: LibraryAction,
  now: string,
): MovieState {
  switch (action.kind) {
    case "rate":
      return {
        ...state,
        rating: action.rating ?? state.rating,
        rating_updated_at: now,
        // Rating implies watched, and the first watched time is preserved.
        watched_at: state.watched_at ?? now,
        watchlisted_at: null,
      };
    case "clear-rating":
      return { ...state, rating: null, rating_updated_at: null };
    case "mark-watched":
      return { ...state, watched_at: state.watched_at ?? now, watchlisted_at: null };
    case "remove-history":
      return { ...state, watched_at: null, rating: null, rating_updated_at: null };
    case "save":
      return { ...state, watchlisted_at: state.watchlisted_at ?? now };
    case "unsave":
      return { ...state, watchlisted_at: null };
    case "dismiss":
      return { ...state, dismissed_at: now, watchlisted_at: null };
    case "undismiss":
      return { ...state, dismissed_at: null };
  }
}

export function replaceMovieState(
  response: LibraryResponse,
  movieId: number,
  next: MovieState,
): LibraryResponse {
  return {
    ...response,
    items: response.items.map((movie) =>
      movie.movie_id === movieId ? { ...movie, state: next } : movie,
    ),
  };
}

export function mapMovieState(
  response: LibraryResponse,
  movieId: number,
  update: (state: MovieState) => MovieState,
): LibraryResponse {
  return {
    ...response,
    items: response.items.map((movie) =>
      movie.movie_id === movieId ? { ...movie, state: update(movie.state) } : movie,
    ),
  };
}

/** Whether a movie still belongs in the collection it is currently listed under. */
export function movieMatchesTab(state: MovieState, tab: LibraryTab): boolean {
  if (tab === "rated") return state.rating !== null;
  if (tab === "watchlist") return state.watchlisted_at !== null;
  return state.watched_at !== null;
}

const LEFT_COLLECTION: Record<LibraryTab, string> = {
  rated: "No longer rated. It leaves Rated when this view reloads.",
  watchlist: "No longer saved. It leaves Watchlist when this view reloads.",
  history: "No longer watched. It leaves History when this view reloads.",
};

export function leftCollectionNote(tab: LibraryTab): string {
  return LEFT_COLLECTION[tab];
}

export function mutationAnnouncement(
  kind: LibraryActionKind,
  title: string,
  persona: string,
): string {
  switch (kind) {
    case "rate":
      return `Rating saved for ${title} in ${persona}'s library. It stays watched history; the star value is display feedback only.`;
    case "clear-rating":
      return `Rating removed from ${title} in ${persona}'s library. It is still watched history.`;
    case "mark-watched":
      return `${title} marked watched in ${persona}'s library.`;
    case "remove-history":
      return `${title} removed from ${persona}'s watched history, along with its rating.`;
    case "save":
      return `${title} saved to ${persona}'s watchlist. Saving does not change recommendations.`;
    case "unsave":
      return `${title} removed from ${persona}'s watchlist.`;
    case "dismiss":
      return `${title} excluded from ${persona}'s recommendations. This can be undone.`;
    case "undismiss":
      return `${title} is eligible for ${persona}'s recommendations again.`;
  }
}

export function rollbackAnnouncement(title: string, persona: string): string {
  return `Could not update ${title}. ${persona}'s saved state was restored.`;
}

export function conflictAnnouncement(title: string, persona: string): string {
  return `${title} changed elsewhere. ${persona}'s latest saved state is shown.`;
}

// UTC keeps a row's date stable across the reviewer's machine, CI, and the
// screenshot harness, which matters when the captures are the evidence.
const DATE_FORMAT = new Intl.DateTimeFormat("en", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

export function formatLibraryDate(value: string | null): string {
  if (!value) return "date unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "date unavailable" : DATE_FORMAT.format(parsed);
}

export function formatRating(rating: number | null): string {
  return rating === null ? "Not rated" : `${rating.toFixed(1)} of 5`;
}

/**
 * The Library payload carries no poster or release year, so a row identifies a
 * movie with a deterministic initial pair rather than a live metadata request.
 */
export function titleInitials(title: string): string {
  const skipped = new Set(["the", "a", "an", "of", "in", "to", "and"]);
  const initials = title
    .split(/\s+/)
    .filter((word) => word && !skipped.has(word.toLowerCase()))
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
  return initials || title.slice(0, 2).toUpperCase() || "?";
}

/**
 * The state line under a title. Every piece of state is spelled out in text so
 * a row never depends on colour alone to say what it is.
 */
export function stateSummary(state: MovieState, tab: LibraryTab): string[] {
  const parts: string[] = [];
  if (tab === "watchlist") {
    parts.push(`Saved ${formatLibraryDate(state.watchlisted_at)}`);
  } else {
    parts.push(`Watched ${formatLibraryDate(state.watched_at)}`);
  }
  if (state.rating !== null) {
    parts.push(`Rated ${formatRating(state.rating)}`);
  } else if (tab !== "watchlist") {
    parts.push("Not rated");
  }
  if (state.dismissed_at) parts.push("Excluded from recommendations");
  return parts;
}
