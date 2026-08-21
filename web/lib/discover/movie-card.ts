/**
 * Live recommendation items rendered through the Bundle 4 poster primitives.
 *
 * `RecommendationItem` and the presentational `MovieCard` are deliberately
 * different shapes: one is the API contract, the other is what a poster needs.
 * Translating in one place keeps the API shape out of the components and gives
 * the small display decisions — the MovieLens title carrying its own year, an
 * absent poster, an unknown state — a single tested home.
 */

import type { MovieState, RecommendationItem } from "@/lib/api";
import type { MovieCard, MovieState as MovieCardState } from "@/lib/movie-types";

const TRAILING_YEAR = /\s*\((\d{4})\)\s*$/;

/**
 * MovieLens titles embed the release year. Showing "Heat (1995)" above a "1995"
 * metadata line reads like a bug, so the year is dropped from the title only
 * when it is also available as structured metadata.
 */
export function displayTitle(title: string, releaseYear: number | null): string {
  if (releaseYear === null) return title;
  const match = TRAILING_YEAR.exec(title);
  return match && Number(match[1]) === releaseYear
    ? title.slice(0, match.index).trim()
    : title;
}

export const UNKNOWN_MOVIE_STATE: MovieCardState = {
  watched: false,
  watchlisted: false,
  rating: null,
  suppressed: false,
};

export function movieCardState(state: MovieState | null | undefined): MovieCardState {
  if (!state) return UNKNOWN_MOVIE_STATE;
  return {
    watched: state.watched_at !== null,
    watchlisted: state.watchlisted_at !== null,
    rating: state.rating,
    suppressed: state.dismissed_at !== null,
  };
}

export function recommendationCard(
  item: RecommendationItem,
  rank: number,
  state?: MovieState | null,
): MovieCard {
  const title = displayTitle(item.title, item.release_year);
  return {
    id: item.movie_id,
    title,
    year: item.release_year,
    genres: item.genres,
    posterSrc: item.poster_url,
    // An empty alt would hide a real poster from assistive technology; a
    // decorative one is never rendered here, so the title carries the meaning.
    posterAlt: item.poster_url ? `Poster for ${title}` : "",
    overview: item.overview,
    reason: item.reason,
    rank,
    state: movieCardState(state),
  };
}

/**
 * The display state a control should show while its mutation is in flight.
 *
 * It mirrors the transitions ADR 0012 §4 pins — watched clears watchlist, a
 * rating implies watched, deleting a rating leaves watched intact — so the
 * optimistic frame matches what the committed response will say. It is a
 * prediction, never a source of truth: the caller reconciles the canonical
 * state on success and restores the previous value on failure.
 */
export function optimisticCardState(
  current: MovieCardState,
  resource: "watched" | "rating" | "watchlist" | "dismissal",
  method: "PUT" | "DELETE",
  rating?: number,
): MovieCardState {
  if (resource === "watchlist") {
    return { ...current, watchlisted: method === "PUT" };
  }
  if (resource === "watched") {
    return method === "PUT"
      ? { ...current, watched: true, watchlisted: false }
      : { ...current, watched: false, rating: null };
  }
  if (resource === "rating") {
    return method === "PUT"
      ? { ...current, watched: true, watchlisted: false, rating: rating ?? null }
      : { ...current, rating: null };
  }
  return method === "PUT"
    ? { ...current, suppressed: true, watchlisted: false }
    : { ...current, suppressed: false };
}

/**
 * Recommendation responses carry no per-item state, so a card starts from
 * "nothing known" and is overlaid with whatever this session has since
 * committed. Anything the overlay does not cover stays honestly unknown.
 */
export function recommendationCards(
  items: readonly RecommendationItem[],
  states: Readonly<Record<number, MovieCardState>> = {},
): MovieCard[] {
  return items.map((item, index) => {
    const card = recommendationCard(item, index + 1);
    const known = states[item.movie_id];
    return known ? { ...card, state: known } : card;
  });
}
