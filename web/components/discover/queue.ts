/**
 * The ranked set as a queue the route owns, not a list it re-reads.
 *
 * `/discover` used to project its primary movie straight out of the last
 * response's first item, which made the featured slot a function of the
 * backend's exclusion set rather than of the viewer's decisions: `Mark watched`
 * and `Not for me` moved it as a side effect of `watched-and-dismissed-
 * excluded-v1`, and `Watchlist` — organizational by ADR 0012, so it excludes
 * nothing — left the same movie sitting there. Pressing a button and watching
 * nothing happen is the reported defect; the button was working.
 *
 * So the queue lives here. `featured` is a cursor position, every decision
 * advances it, and the rail is what is still ahead. Three rules make that safe:
 *
 * - **The cursor only moves on a commit.** A rolled-back advance would re-show
 *   a title the viewer had already dismissed, which is the reason
 *   `lib/quick-picks/contract.ts` gives for the same rule one route over.
 * - **A refetch merges behind the cursor.** The post-mutation re-read is what
 *   keeps the watch-history region and the exclusion set honest, but replacing
 *   the card being read mid-decision is part of what made the old behaviour
 *   feel broken. Everything up to and including the current card is kept; the
 *   tail is rebuilt from the freshest response, which is also how a title
 *   excluded elsewhere disappears from what is still ahead.
 * - **An acted-on title never comes back.** The backend drops watched and
 *   dismissed titles on its own, but a watchlisted one keeps being returned —
 *   correctly, since saving it changes no model input. The session's own record
 *   of what it has decided is what keeps it from being offered twice.
 *
 * Kept free of React so the interesting rules are unit-testable directly.
 */

import type { RecommendationItem } from "@/lib/api";

/**
 * Extend once the cursor is this close to the end. Deep enough that the fetch
 * resolves long before the viewer arrives, shallow enough that a persona with a
 * short catalog is not refetching after every decision.
 */
export const QUEUE_EXTENSION_TRIGGER = 3;

export type DiscoverQueue = {
  items: readonly RecommendationItem[];
  cursor: number;
  /** Decided in this session, in the order the decisions were made. */
  acted: readonly number[];
};

export function initialQueue(items: readonly RecommendationItem[]): DiscoverQueue {
  return { items, cursor: 0, acted: [] };
}

export function featuredItem(queue: DiscoverQueue): RecommendationItem | null {
  return queue.items[queue.cursor] ?? null;
}

/** What the rail shows: everything still ahead of the featured movie. */
export function upcomingItems(queue: DiscoverQueue): readonly RecommendationItem[] {
  return queue.items.slice(queue.cursor + 1);
}

/** Titles still queued after the one being read. Drives the extension. */
export function remainingAfterFeatured(queue: DiscoverQueue): number {
  return Math.max(queue.items.length - queue.cursor - 1, 0);
}

export function mergeBehindCursor(
  queue: DiscoverQueue,
  incoming: readonly RecommendationItem[],
): DiscoverQueue {
  const head = queue.items.slice(0, queue.cursor + 1);
  const seen = new Set(head.map((item) => item.movie_id));
  const acted = new Set(queue.acted);
  const tail: RecommendationItem[] = [];
  for (const item of incoming) {
    if (seen.has(item.movie_id) || acted.has(item.movie_id)) continue;
    seen.add(item.movie_id);
    tail.push(item);
  }
  return { ...queue, items: [...head, ...tail] };
}

/**
 * Records a committed decision. The featured slot moves on; a rail card leaves
 * what is still ahead without disturbing the movie being read — offering it
 * again as the next decision would be the same "nothing happened" the queue
 * exists to fix.
 */
export function recordDecision(queue: DiscoverQueue, movieId: number): DiscoverQueue {
  const acted = queue.acted.includes(movieId) ? queue.acted : [...queue.acted, movieId];
  if (featuredItem(queue)?.movie_id === movieId) {
    return { ...queue, cursor: queue.cursor + 1, acted };
  }
  return {
    ...queue,
    items: queue.items.filter(
      (item, index) => index <= queue.cursor || item.movie_id !== movieId,
    ),
    acted,
  };
}

/**
 * Puts the cursor back on a title whose decision was undone. Both halves
 * matter: without dropping it from `acted` the next merge would filter it out
 * of its own position.
 */
export function restoreQueue(queue: DiscoverQueue, movieId: number): DiscoverQueue {
  return {
    ...queue,
    cursor: Math.max(queue.cursor - 1, 0),
    acted: queue.acted.filter((id) => id !== movieId),
  };
}
