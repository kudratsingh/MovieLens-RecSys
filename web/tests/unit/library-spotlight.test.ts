import { describe, expect, it } from "vitest";

import {
  EMPTY_SPOTLIGHT,
  initialSpotlight,
  isFirstSpotlight,
  isLastSpotlight,
  nextSpotlight,
  previousSpotlight,
  removeCurrent,
  shouldExtendSpotlight,
  spotlightMovieId,
  syncToWindow,
} from "@/lib/library/spotlight";

const WINDOW = [101, 102, 103, 104, 105];

describe("the Seen spotlight clamps rather than wrapping", () => {
  it("starts on the first loaded title", () => {
    const start = initialSpotlight(WINDOW);
    expect(spotlightMovieId(start)).toBe(101);
    expect(isFirstSpotlight(start)).toBe(true);
    expect(isLastSpotlight(start)).toBe(false);
  });

  it("stops at both ends instead of returning to the other one", () => {
    // A `Next` that silently reopens the first title is a press that did
    // nothing; the control is disabled at the end instead.
    const start = initialSpotlight(WINDOW);
    expect(spotlightMovieId(previousSpotlight(start))).toBe(101);

    const end = { index: 4, movieIds: WINDOW };
    expect(isLastSpotlight(end)).toBe(true);
    expect(spotlightMovieId(nextSpotlight(end))).toBe(105);
  });

  it("renders nothing for an empty window", () => {
    expect(spotlightMovieId(EMPTY_SPOTLIGHT)).toBeNull();
    expect(nextSpotlight(EMPTY_SPOTLIGHT)).toEqual(EMPTY_SPOTLIGHT);
    expect(previousSpotlight(EMPTY_SPOTLIGHT)).toEqual(EMPTY_SPOTLIGHT);
    // Both controls are disabled: there is no first and no last.
    expect(isFirstSpotlight(EMPTY_SPOTLIGHT)).toBe(true);
    expect(isLastSpotlight(EMPTY_SPOTLIGHT)).toBe(true);
  });
});

describe("the spotlight follows the movie, not the index", () => {
  it("keeps its title when a cursor page is appended", () => {
    const held = { index: 3, movieIds: WINDOW };
    const appended = syncToWindow(held, [...WINDOW, 106, 107, 108]);

    expect(spotlightMovieId(appended)).toBe(104);
    expect(appended.index).toBe(3);
  });

  it("keeps its title when a row above it leaves the collection", () => {
    const held = { index: 3, movieIds: WINDOW };
    const shrunk = syncToWindow(held, [101, 103, 104, 105]);

    expect(spotlightMovieId(shrunk)).toBe(104);
    expect(shrunk.index).toBe(2);
  });

  it("holds the position when the title itself is gone", () => {
    const held = { index: 3, movieIds: WINDOW };
    const without = syncToWindow(held, [101, 102, 103, 105]);

    expect(without.index).toBe(3);
    expect(spotlightMovieId(without)).toBe(105);
  });

  it("collapses to nothing when the window empties", () => {
    expect(syncToWindow({ index: 3, movieIds: WINDOW }, [])).toEqual({
      index: 0,
      movieIds: [],
    });
  });
});

describe("removing the current title advances the spotlight", () => {
  it("hands the position to the title that took its place", () => {
    const held = { index: 2, movieIds: WINDOW };
    const removed = removeCurrent(held, 103);

    expect(removed.movieIds).toEqual([101, 102, 104, 105]);
    expect(spotlightMovieId(removed)).toBe(104);
  });

  it("clamps to the last row when the removed title was last", () => {
    const end = { index: 4, movieIds: WINDOW };
    const removed = removeCurrent(end, 105);

    expect(removed.index).toBe(3);
    expect(spotlightMovieId(removed)).toBe(104);
  });

  it("does not drag the reader forward when the removal was behind them", () => {
    const held = { index: 3, movieIds: WINDOW };
    const removed = removeCurrent(held, 101);

    expect(spotlightMovieId(removed)).toBe(104);
  });

  it("leaves a window that never held the title alone", () => {
    const held = { index: 1, movieIds: WINDOW };
    expect(removeCurrent(held, 999)).toBe(held);
  });

  it("empties cleanly when the last remaining title is removed", () => {
    expect(removeCurrent({ index: 0, movieIds: [101] }, 101)).toEqual({
      index: 0,
      movieIds: [],
    });
  });
});

describe("the spotlight asks for the next page before it needs it", () => {
  it("extends once the reader is within the trigger depth of the end", () => {
    expect(shouldExtendSpotlight({ index: 0, movieIds: WINDOW })).toBe(false);
    expect(shouldExtendSpotlight({ index: 1, movieIds: WINDOW })).toBe(false);
    expect(shouldExtendSpotlight({ index: 2, movieIds: WINDOW })).toBe(true);
    expect(shouldExtendSpotlight({ index: 4, movieIds: WINDOW })).toBe(true);
  });
});
