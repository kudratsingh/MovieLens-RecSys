/**
 * Pure collection logic for the Library route.
 *
 * The component that uses this is busy with focus, announcements, and network
 * state, so the parts that are easy to get subtly wrong — page appends and the
 * copy that describes a collection — live here where they can be tested
 * directly.
 *
 * What is deliberately *not* here any more: the transition table, the request
 * mapping, and the mutation vocabulary. Those describe what a watched, rating,
 * watchlist, or dismissal change means, which is the same on every route, so
 * they live in `lib/movie-state/` with the write path that enforces them. This
 * module keeps only what is genuinely about a Library collection.
 */

import type { LibraryMovie, LibraryResponse, MovieState } from "@/lib/api";
import type { MovieStateAction } from "@/lib/movie-state/actions";
import type { LibraryTab } from "@/lib/library/url-state";

/**
 * Only a rating change — or a history removal, which takes the rating with it —
 * can move the rating-derived taste summary.
 */
export function affectsTasteSummary(action: MovieStateAction): boolean {
  return (
    action.resource === "rating" ||
    (action.resource === "watched" && action.method === "DELETE")
  );
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
  history: "No longer watched. It leaves Seen when this view reloads.",
};

export function leftCollectionNote(tab: LibraryTab): string {
  return LEFT_COLLECTION[tab];
}

// UTC keeps a row's date stable across the reviewer's machine, CI, and the
// screenshot harness, which matters when the captures are the evidence.
const DATE_FORMAT = new Intl.DateTimeFormat("en", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
  year: "numeric",
});

const UNAVAILABLE_DATE = "date unavailable";

export function formatLibraryDate(value: string | null): string {
  if (!value) return UNAVAILABLE_DATE;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? UNAVAILABLE_DATE : DATE_FORMAT.format(parsed);
}

/**
 * The spotlight's own date line.
 *
 * It uses `formatLibraryDate`, so a capture and a runner print the same day for
 * the same row — but it names the gap differently: a spotlight sentence reading
 * "Seen on date unavailable" is a formatter leaking into prose.
 */
export function seenOnText(watchedAt: string | null): string {
  const date = formatLibraryDate(watchedAt);
  return date === UNAVAILABLE_DATE ? "Seen on an unknown date" : `Seen on ${date}`;
}

/**
 * The compact crowd-score mark on a row.
 *
 * Null when there is no score, and the row then shows nothing in its place. A
 * missing synopsis is named because the reader is looking for one; a missing
 * crowd score beside a movie is noise. The vote count is deliberately absent —
 * the row has no room for it, and the detail record is where the count travels
 * with the average.
 */
export function tmdbMarkText(rating: number | null | undefined): string | null {
  if (typeof rating !== "number" || !Number.isFinite(rating) || rating <= 0) {
    return null;
  }
  return `TMDB ${rating.toFixed(1)}`;
}

export function formatRating(rating: number | null): string {
  return rating === null ? "Not rated" : `${rating.toFixed(1)} of 5`;
}

/**
 * The metadata line under a row's title.
 *
 * The year moved here when the payload started carrying it: the title itself is
 * run through `displayTitle`, so printing "Babe (1995)" above a line that also
 * says 1995 is exactly what that rule exists to prevent. A row with neither
 * still says something rather than collapsing to an empty line.
 */
export function movieMetaLine(
  releaseYear: number | null,
  genres: readonly string[],
): string {
  const parts = [
    // Tested for a number rather than against `null`: the API and the web app
    // are separate images, so a backend that predates the field sends no key at
    // all, and `String(undefined)` would print the word "undefined" at the top
    // of every row.
    typeof releaseYear === "number" && Number.isFinite(releaseYear)
      ? String(releaseYear)
      : null,
    genres.length ? genres.join(" · ") : "Genres unavailable",
  ];
  return parts.filter((part): part is string => part !== null).join(" · ");
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
