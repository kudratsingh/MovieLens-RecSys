/**
 * Live recommendation items rendered through the Bundle 4 poster primitives.
 *
 * `RecommendationItem` and the presentational `MovieCard` are deliberately
 * different shapes: one is the API contract, the other is what a poster needs.
 * Translating in one place keeps the API shape out of the components and gives
 * the small display decisions — the MovieLens title carrying its own year, an
 * absent poster, an unknown state — a single tested home.
 *
 * The state projection and the optimistic transitions are *not* here. They are
 * shared with every other surface in `lib/movie-state/actions.ts`, because a
 * recommendation card and a Library row have to agree about what marking a
 * movie watched does.
 */

import type { MovieState, RecommendationItem } from "@/lib/api";
import { displayState, type MovieDisplayState } from "@/lib/movie-state/actions";
import type { MovieCard } from "@/lib/movie-types";

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
    state: displayState(state),
  };
}

/**
 * Recommendation responses carry no per-item state, so a card starts from
 * "nothing known" and is overlaid with whatever the route has since learned —
 * this session's own commits, and the states other routes relayed through the
 * committed store. Anything the overlay does not cover stays honestly unknown.
 */
export function recommendationCards(
  items: readonly RecommendationItem[],
  states: Readonly<Record<number, MovieDisplayState>> = {},
): MovieCard[] {
  return items.map((item, index) => {
    const card = recommendationCard(item, index + 1);
    const known = states[item.movie_id];
    return known ? { ...card, state: known } : card;
  });
}
