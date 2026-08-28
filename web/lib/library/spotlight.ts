/**
 * Where the Seen spotlight is pointing, as a value rather than as a hook.
 *
 * The spotlight walks the loaded window of Seen rows: same filters, same sort,
 * same order, so a position is an index into the list the reader can already
 * see. That makes the interesting rules small and worth testing on their own —
 * which is the same reason `components/discover/queue.ts` is not a hook either.
 *
 * Three of them carry the weight:
 *
 * - **It clamps and never wraps.** A `Next` that silently returns to the first
 *   title is a press that did nothing, so at either end the control is disabled
 *   instead.
 * - **It follows the movie, not the index.** Appending a cursor page, re-reading
 *   after a write, or a row leaving the collection all move indices around; the
 *   spotlight stays on the title the reader is looking at. Only when that title
 *   is gone does the index become the fallback.
 * - **A removal advances rather than retreats.** Taking the current title out
 *   leaves the index where it is, so the title that took its place becomes
 *   current — and clamps to the last row when the removed one was last.
 *
 * `nextSpotlight` / `previousSpotlight` carry the noun on purpose: a bare
 * `next` export reads as a promise resolver at every call site that imports it.
 */

export type SpotlightState = {
  index: number;
  movieIds: readonly number[];
};

/**
 * Extend the window once the spotlight is this close to the end of it. The same
 * depth Discover's queue uses: deep enough that the page arrives before the
 * reader does, shallow enough that a short collection is not refetching after
 * every press.
 */
export const SPOTLIGHT_EXTENSION_TRIGGER = 3;

export const EMPTY_SPOTLIGHT: SpotlightState = { index: 0, movieIds: [] };

function clamp(index: number, length: number): number {
  if (length === 0) return 0;
  return Math.min(Math.max(index, 0), length - 1);
}

export function initialSpotlight(movieIds: readonly number[]): SpotlightState {
  return { index: 0, movieIds };
}

/** The movie the spotlight is on, or null when the window is empty. */
export function spotlightMovieId(state: SpotlightState): number | null {
  return state.movieIds[state.index] ?? null;
}

export function isFirstSpotlight(state: SpotlightState): boolean {
  return state.index <= 0;
}

export function isLastSpotlight(state: SpotlightState): boolean {
  return state.index >= state.movieIds.length - 1;
}

export function nextSpotlight(state: SpotlightState): SpotlightState {
  return { ...state, index: clamp(state.index + 1, state.movieIds.length) };
}

export function previousSpotlight(state: SpotlightState): SpotlightState {
  return { ...state, index: clamp(state.index - 1, state.movieIds.length) };
}

/**
 * Adopts a new window, keeping the spotlight on the same movie where it can.
 *
 * The identity check is what makes an appended page invisible to the reader: a
 * window that grew at the tail leaves every earlier index alone, and one that
 * lost a row above the current title would otherwise shift the reader forward
 * by one without anything having been pressed.
 */
export function syncToWindow(
  state: SpotlightState,
  movieIds: readonly number[],
): SpotlightState {
  const current = spotlightMovieId(state);
  const found = current === null ? -1 : movieIds.indexOf(current);
  return {
    index: found >= 0 ? found : clamp(state.index, movieIds.length),
    movieIds,
  };
}

/**
 * Drops a title the reader has just taken out of the collection.
 *
 * Stated at the write rather than left to the next window sync, because the
 * spotlight must not spend a render pointing at a movie that is on its way out:
 * the announcement and the focus walk both name what is current *now*.
 */
export function removeCurrent(
  state: SpotlightState,
  movieId: number,
): SpotlightState {
  const removed = state.movieIds.indexOf(movieId);
  if (removed < 0) return state;
  const movieIds = state.movieIds.filter((id) => id !== movieId);
  // A title removed from behind the spotlight would otherwise pull a different
  // movie under the same index; only a removal at or after it advances.
  const index = removed < state.index ? state.index - 1 : state.index;
  return { index: clamp(index, movieIds.length), movieIds };
}

/** Whether the reader is close enough to the end to be worth paging ahead. */
export function shouldExtendSpotlight(state: SpotlightState): boolean {
  return state.index >= state.movieIds.length - SPOTLIGHT_EXTENSION_TRIGGER;
}
