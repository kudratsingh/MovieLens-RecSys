/**
 * The accumulated catalog window behind Browse.
 *
 * Cursor pagination gives us pages, but the viewer sees one growing grid, so
 * something has to own the join. Keeping it as pure functions means the two
 * properties that actually matter — no duplicate cards, and an order that
 * never reshuffles under a scrolled viewer — are testable without a browser.
 *
 * The endpoint's ordering is already deterministic (sort value then movie ID),
 * so appending in arrival order preserves it. Dedupe keeps the *first*
 * occurrence for the same reason: an item that reappears must not jump.
 */

import type { CatalogItem, CatalogResponse, MovieState } from "@/lib/api";

export type CatalogWindow = {
  /** The filter set these items belong to; see `browseFilterKey`. */
  filterKey: string;
  items: readonly CatalogItem[];
  nextCursor: string | null;
  hasMore: boolean;
  /** Set when the URL asked us to resume mid-catalog rather than at the top. */
  resumedFrom: string | null;
  /** Set when a cursor was rejected and the window restarted at the top. */
  restartedFromStaleCursor: boolean;
  pagesLoaded: number;
};

export function startWindow(
  filterKey: string,
  resumeCursor: string | null = null,
): CatalogWindow {
  return {
    filterKey,
    items: [],
    nextCursor: null,
    hasMore: false,
    resumedFrom: resumeCursor,
    restartedFromStaleCursor: false,
    pagesLoaded: 0,
  };
}

export function appendCatalogPage(
  window: CatalogWindow,
  response: CatalogResponse,
): CatalogWindow {
  const seen = new Set(window.items.map((item) => item.movie_id));
  const added: CatalogItem[] = [];
  for (const item of response.items) {
    if (seen.has(item.movie_id)) continue;
    seen.add(item.movie_id);
    added.push(item);
  }

  return {
    ...window,
    items: added.length ? [...window.items, ...added] : window.items,
    nextCursor: response.page.next_cursor,
    hasMore: response.page.has_more && Boolean(response.page.next_cursor),
    pagesLoaded: window.pagesLoaded + 1,
  };
}

/**
 * The endpoint rejected our cursor, which means the query moved underneath it.
 * Restart at the top of the same filter set and remember why, so the UI can
 * say so plainly instead of showing an error where results should be.
 */
export function restartAfterStaleCursor(window: CatalogWindow): CatalogWindow {
  return {
    ...startWindow(window.filterKey),
    restartedFromStaleCursor: true,
  };
}

/**
 * Replaces one card's durable state after a committed mutation. Position is
 * untouched on purpose — a watchlist toggle must not move the grid under a
 * viewer's pointer.
 */
export function replaceMovieState(
  window: CatalogWindow,
  movieId: number,
  state: MovieState | null,
): CatalogWindow {
  let changed = false;
  const items = window.items.map((item) => {
    if (item.movie_id !== movieId) return item;
    changed = true;
    return { ...item, state };
  });
  return changed ? { ...window, items } : window;
}

export function windowIsEmpty(window: CatalogWindow): boolean {
  return window.items.length === 0;
}
