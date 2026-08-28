import { describe, expect, it } from "vitest";

import {
  featuredIndex,
  featuredItem,
  initialQueue,
  mergeBehindCursor,
  recordDecision,
  remainingAfterFeatured,
  restoreQueue,
  skipFeatured,
  upcomingItems,
  type DiscoverQueue,
} from "@/components/discover/queue";
import type { RecommendationItem } from "@/lib/api";
import { learnedRecommendations } from "@/lib/fixtures/discover-fixtures";

const items = learnedRecommendations.items;

function ids(queue: DiscoverQueue): number[] {
  return queue.items.map((item) => item.movie_id);
}

function itemsFor(movieIds: readonly number[]): RecommendationItem[] {
  return movieIds.map(
    (movieId) => items.find((item) => item.movie_id === movieId) ?? items[0],
  );
}

describe("the featured slot is a cursor", () => {
  it("reads the movie at the cursor and rails whatever is ahead of it", () => {
    const queue = initialQueue(items.slice(0, 3));

    expect(featuredItem(queue)?.movie_id).toBe(101);
    expect(upcomingItems(queue).map((item) => item.movie_id)).toEqual([102, 103]);
    expect(remainingAfterFeatured(queue)).toBe(2);
  });

  it("moves on when the featured movie is decided", () => {
    const after = recordDecision(initialQueue(items.slice(0, 3)), 101);

    expect(featuredItem(after)?.movie_id).toBe(102);
    expect(after.acted).toEqual([101]);
  });

  it("drops a decided rail card without moving the movie being read", () => {
    const after = recordDecision(initialQueue(items.slice(0, 3)), 103);

    expect(featuredItem(after)?.movie_id).toBe(101);
    expect(ids(after)).toEqual([101, 102]);
    expect(after.acted).toEqual([103]);
  });

  it("runs out rather than wrapping", () => {
    const spent = recordDecision(initialQueue(items.slice(0, 1)), 101);

    expect(featuredItem(spent)).toBeNull();
    expect(remainingAfterFeatured(spent)).toBe(0);
  });
});

describe("a refetch merges behind the cursor", () => {
  it("leaves the card being read exactly where it is", () => {
    const queue = recordDecision(initialQueue(items.slice(0, 4)), 101);
    // A fresh response, in a different order, with the read card still in it.
    const merged = mergeBehindCursor(queue, itemsFor([105, 102, 104, 106]));

    expect(featuredItem(merged)?.movie_id).toBe(102);
    expect(ids(merged)).toEqual([101, 102, 105, 104, 106]);
  });

  it("rebuilds the tail from the response, so a title excluded elsewhere leaves", () => {
    const queue = initialQueue(items.slice(0, 4));
    const merged = mergeBehindCursor(queue, itemsFor([102, 104]));

    // 103 was in the previous tail and is not in the new answer.
    expect(ids(merged)).toEqual([101, 102, 104]);
  });

  it("never re-offers a title this session has already decided", () => {
    // Watchlist excludes nothing server-side, so the API keeps returning it.
    // 103 is the one that matters: it is behind the cursor in the old queue and
    // present in the answer, and it must not be re-inserted ahead of the read.
    const queue = recordDecision(initialQueue(items.slice(0, 2)), 101);
    const merged = mergeBehindCursor(queue, itemsFor([101, 103, 104]));

    expect(merged.items.slice(merged.cursor).map((item) => item.movie_id)).toEqual([
      102, 103, 104,
    ]);
  });

  it("appends without disturbing anything when it extends", () => {
    const queue = initialQueue(items.slice(0, 3));
    const merged = mergeBehindCursor(queue, itemsFor([101, 102, 103, 104, 105]));

    expect(featuredItem(merged)?.movie_id).toBe(101);
    expect(ids(merged)).toEqual([101, 102, 103, 104, 105]);
  });
});

describe("undo puts back both halves of a decision", () => {
  it("returns the cursor and clears the record that would filter it out", () => {
    const after = recordDecision(initialQueue(items.slice(0, 3)), 101);
    const undone = restoreQueue(after, 101);

    expect(featuredItem(undone)?.movie_id).toBe(101);
    expect(undone.acted).toEqual([]);
    // …so the next merge cannot quietly remove it again.
    expect(ids(mergeBehindCursor(undone, itemsFor([101, 102, 103])))).toEqual([
      101, 102, 103,
    ]);
  });

  it("cannot walk the cursor behind the start of the queue", () => {
    expect(restoreQueue(initialQueue(items.slice(0, 2)), 101).cursor).toBe(0);
  });
});

describe("a title can lose the featured slot without leaving the set", () => {
  /** The shape the route builds: skipped by hand, or held back by the setting. */
  const heldBack = (...movieIds: number[]) => (movieId: number) =>
    movieIds.includes(movieId);

  it("hands the slot to the next eligible title", () => {
    const queue = initialQueue(items.slice(0, 4));

    expect(featuredItem(queue, heldBack(101, 102))?.movie_id).toBe(103);
    expect(featuredIndex(queue, heldBack(101, 102))).toBe(2);
  });

  it("keeps a held-back title in the rail rather than dropping it", () => {
    // This is the whole difference between a pass-over and a decision: the
    // title is still a recommendation, so it is still on the page.
    const queue = initialQueue(items.slice(0, 4));

    expect(upcomingItems(queue, heldBack(101)).map((item) => item.movie_id)).toEqual([
      101, 103, 104,
    ]);
  });

  it("counts only what could still be featured, so the queue tops itself up", () => {
    const queue = initialQueue(items.slice(0, 4));

    expect(remainingAfterFeatured(queue, heldBack(102, 103, 104))).toBe(0);
    expect(remainingAfterFeatured(queue, heldBack(101, 102, 103, 104))).toBe(0);
    expect(featuredItem(queue, heldBack(101, 102, 103, 104))).toBeNull();
  });

  it("records a skip without recording a decision", () => {
    const queue = skipFeatured(initialQueue(items.slice(0, 3)), 101);

    expect(queue.skipped).toEqual([101]);
    // `acted` is what filters a title out of future merges. A skip is not a
    // decision, so the title stays eligible to come back.
    expect(queue.acted).toEqual([]);
    expect(skipFeatured(queue, 101).skipped).toEqual([101]);
  });

  it("never replaces the card being read, even when it is past the cursor", () => {
    const queue = skipFeatured(initialQueue(items.slice(0, 4)), 101);
    const passOver = heldBack(101);
    // A response in a different order arrives while 102 is on screen.
    const merged = mergeBehindCursor(queue, itemsFor([106, 105, 102]), passOver);

    expect(featuredItem(merged, passOver)?.movie_id).toBe(102);
    expect(ids(merged)).toEqual([101, 102, 106, 105]);
  });

  it("removes a decided title that was not at the cursor", () => {
    // With 101 skipped, the featured card sits at index 1. Deciding it must
    // take it out of the set without dragging the skipped title with it.
    const queue = recordDecision(skipFeatured(initialQueue(items.slice(0, 3)), 101), 102);

    expect(ids(queue)).toEqual([101, 103]);
    expect(queue.cursor).toBe(0);
    expect(featuredItem(queue, heldBack(101))?.movie_id).toBe(103);
  });

  it("clears the skip when the title is finally decided", () => {
    const queue = recordDecision(skipFeatured(initialQueue(items.slice(0, 3)), 101), 101);

    expect(queue.skipped).toEqual([]);
    expect(queue.acted).toEqual([101]);
    expect(queue.cursor).toBe(1);
  });

  it("does not walk the cursor back over a title the undo was not about", () => {
    // 102 was decided from the featured slot while 101 was skipped, so the
    // cursor never moved. Undoing it must not drag the viewer back past 101.
    const decided = recordDecision(skipFeatured(initialQueue(items.slice(0, 3)), 101), 102);

    expect(restoreQueue(decided, 102).cursor).toBe(0);
  });
});
