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
import { displayTitle, type MovieCard } from "@/lib/movie-types";

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
    // A poster that loads sits directly beside the visible title and inside a
    // link that already names the movie, so it is decorative and takes an empty
    // alt. The same component on Browse always did this; "Poster for Heat"
    // announced next to "Heat" is the duplication, not the accommodation.
    posterAlt: "",
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
