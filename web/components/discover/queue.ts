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
 * So the queue lives here. `cursor` is how far the viewer has decided, the
 * featured slot is the first title at or after it that is eligible, and the
 * rail is everything else still in the set. Four rules make that safe:
 *
 * - **The cursor only moves on a commit.** A rolled-back advance would re-show
 *   a title the viewer had already dismissed, which is the reason
 *   `lib/quick-picks/contract.ts` gives for the same rule one route over.
 * - **A refetch merges behind the featured card.** The post-mutation re-read is
 *   what keeps the watch-history region and the exclusion set honest, but
 *   replacing the card being read mid-decision is part of what made the old
 *   behaviour feel broken. Everything up to and including the card on screen is
 *   kept; the tail is rebuilt from the freshest response, which is also how a
 *   title excluded elsewhere disappears from what is still ahead.
 * - **An acted-on title never comes back.** The backend drops watched and
 *   dismissed titles on its own, but a watchlisted one keeps being returned —
 *   correctly, since saving it changes no model input. The session's own record
 *   of what it has decided is what keeps it from being offered twice.
 * - **Passing over is not deciding.** A title the viewer skipped, or one the
 *   `Featured picks` preference holds back, loses the featured slot and keeps
 *   its place in the rail. Nothing is written, nothing is excluded, and the
 *   title is still a recommendation — so `acted` is the wrong list for it and
 *   `skipped` is its own.
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

/**
 * Titles that may not take the featured slot. Applied by movie id rather than
 * by item so the caller can build it from state it holds — the preference, the
 * per-card states, this session's skips — without the queue knowing any of that.
 */
export type FeaturedPassOver = (movieId: number) => boolean;

const NOTHING_PASSED_OVER: FeaturedPassOver = () => false;

export type DiscoverQueue = {
  items: readonly RecommendationItem[];
  cursor: number;
  /** Decided in this session, in the order the decisions were made. */
  acted: readonly number[];
  /** Passed over by hand in this session. Not a decision, and never a signal. */
  skipped: readonly number[];
};

export function initialQueue(items: readonly RecommendationItem[]): DiscoverQueue {
  return { items, cursor: 0, acted: [], skipped: [] };
}

/**
 * Where the featured slot is pointing, or `-1` when nothing left is eligible.
 *
 * Scanning forward from the cursor rather than taking `items[cursor]` is the
 * whole of the pass-over mechanism: a held-back title keeps its index, so it
 * stays in the rail and stays available the moment the preference changes back.
 */
export function featuredIndex(
  queue: DiscoverQueue,
  passOver: FeaturedPassOver = NOTHING_PASSED_OVER,
): number {
  for (let index = queue.cursor; index < queue.items.length; index += 1) {
    if (!passOver(queue.items[index].movie_id)) return index;
  }
  return -1;
}

export function featuredItem(
  queue: DiscoverQueue,
  passOver: FeaturedPassOver = NOTHING_PASSED_OVER,
): RecommendationItem | null {
  const index = featuredIndex(queue, passOver);
  return index < 0 ? null : queue.items[index];
}

/** What the rail shows: everything still in the set except the featured card. */
export function upcomingItems(
  queue: DiscoverQueue,
  passOver: FeaturedPassOver = NOTHING_PASSED_OVER,
): readonly RecommendationItem[] {
  const featured = featuredIndex(queue, passOver);
  return queue.items.filter(
    (_, index) => index >= queue.cursor && index !== featured,
  );
}

/**
 * Titles that could still take the featured slot after this one. Drives the
 * extension, so it counts eligibility rather than length: a queue whose whole
 * tail is held back has nothing to feature next and needs topping up exactly as
 * much as an empty one does.
 */
export function remainingAfterFeatured(
  queue: DiscoverQueue,
  passOver: FeaturedPassOver = NOTHING_PASSED_OVER,
): number {
  let eligible = 0;
  for (let index = queue.cursor; index < queue.items.length; index += 1) {
    if (!passOver(queue.items[index].movie_id)) eligible += 1;
  }
  return Math.max(eligible - 1, 0);
}

export function mergeBehindCursor(
  queue: DiscoverQueue,
  incoming: readonly RecommendationItem[],
  passOver: FeaturedPassOver = NOTHING_PASSED_OVER,
): DiscoverQueue {
  // The head reaches the card on screen, which is at or past the cursor: a
  // merge that rebuilt the tail from the cursor could replace the movie being
  // read whenever something ahead of it was being held back.
  const head = queue.items.slice(
    0,
    Math.max(queue.cursor, featuredIndex(queue, passOver)) + 1,
  );
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
 * Records a committed decision. A title at the cursor moves it on; anything
 * else leaves the set without disturbing the movie being read — offering it
 * again as the next decision would be the same "nothing happened" the queue
 * exists to fix.
 *
 * The cursor test names the item at the cursor rather than the featured one,
 * because with a pass-over in force those are not always the same index. A
 * decision on a featured card further along removes it the way a rail decision
 * does, which leaves the held-back titles between them exactly where they are.
 */
export function recordDecision(queue: DiscoverQueue, movieId: number): DiscoverQueue {
  const acted = queue.acted.includes(movieId) ? queue.acted : [...queue.acted, movieId];
  const skipped = queue.skipped.filter((id) => id !== movieId);
  if (queue.items[queue.cursor]?.movie_id === movieId) {
    return { ...queue, cursor: queue.cursor + 1, acted, skipped };
  }
  return {
    ...queue,
    items: queue.items.filter(
      (item, index) => index < queue.cursor || item.movie_id !== movieId,
    ),
    acted,
    skipped,
  };
}

/**
 * Passes the featured title over for the rest of this session.
 *
 * It writes nothing anywhere else, by design (ADR 0012's 2026-08-28 note): the
 * title keeps its watchlist entry, its place in the ranked set, and its rail
 * card. All it loses is the featured slot.
 */
export function skipFeatured(queue: DiscoverQueue, movieId: number): DiscoverQueue {
  if (queue.skipped.includes(movieId)) return queue;
  return { ...queue, skipped: [...queue.skipped, movieId] };
}

/**
 * Puts the cursor back on a title whose decision was undone. Both halves
 * matter: without dropping it from `acted` the next merge would filter it out
 * of its own position.
 *
 * The cursor moves back only when the title it would move back onto is the one
 * being restored. A decision taken on a card that was not at the cursor never
 * moved it, so walking it backwards would drag the viewer past an unrelated
 * movie they had not yet answered.
 */
export function restoreQueue(queue: DiscoverQueue, movieId: number): DiscoverQueue {
  const cursor =
    queue.items[queue.cursor - 1]?.movie_id === movieId ? queue.cursor - 1 : queue.cursor;
  return {
    ...queue,
    cursor: Math.max(cursor, 0),
    acted: queue.acted.filter((id) => id !== movieId),
  };
}
