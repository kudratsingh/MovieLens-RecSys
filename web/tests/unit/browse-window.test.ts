import { describe, expect, it } from "vitest";

import type { CatalogItem, CatalogResponse } from "@/lib/api";
import {
  appendCatalogPage,
  replaceMovieState,
  restartAfterStaleCursor,
  startWindow,
  windowIsEmpty,
} from "@/lib/browse/window";
import { catalogResponse, movieState } from "./resource-fixtures";

const template = catalogResponse.items[0];

function item(movieId: number, title = `Title ${movieId}`): CatalogItem {
  return { ...template, movie_id: movieId, title, state: null };
}

function page(
  items: CatalogItem[],
  next: string | null = null,
): CatalogResponse {
  return {
    ...catalogResponse,
    items,
    page: { has_more: next !== null, next_cursor: next },
  };
}

describe("appending a cursor page", () => {
  it("keeps arrival order, which is the endpoint's order", () => {
    const window = appendCatalogPage(
      appendCatalogPage(startWindow("q=x"), page([item(1), item(2)], "c1")),
      page([item(3), item(4)]),
    );

    expect(window.items.map((entry) => entry.movie_id)).toEqual([1, 2, 3, 4]);
    expect(window.pagesLoaded).toBe(2);
  });

  it("drops a repeated movie instead of showing it twice", () => {
    const window = appendCatalogPage(
      appendCatalogPage(startWindow("q=x"), page([item(1), item(2)], "c1")),
      page([item(2), item(3)]),
    );

    expect(window.items.map((entry) => entry.movie_id)).toEqual([1, 2, 3]);
  });

  it("keeps the first occurrence in place so nothing jumps under a scroll", () => {
    const first = appendCatalogPage(
      startWindow("q=x"),
      page([item(1, "Original"), item(2)], "c1"),
    );
    const second = appendCatalogPage(first, page([item(1, "Repeated")]));

    expect(second.items[0]).toMatchObject({ movie_id: 1, title: "Original" });
    expect(second.items).toHaveLength(2);
  });

  it("treats a page with no cursor as the end even if the flag disagrees", () => {
    const window = appendCatalogPage(startWindow("q=x"), {
      ...page([item(1)]),
      page: { has_more: true, next_cursor: null },
    });

    expect(window.hasMore).toBe(false);
  });

  it("carries the next cursor forward", () => {
    expect(appendCatalogPage(startWindow("q=x"), page([item(1)], "c1"))).toMatchObject(
      { hasMore: true, nextCursor: "c1" },
    );
  });
});

describe("restarting after a rejected cursor", () => {
  it("empties the window and records why", () => {
    const paged = appendCatalogPage(
      startWindow("q=x", "stale-cursor"),
      page([item(1)], "c1"),
    );
    const restarted = restartAfterStaleCursor(paged);

    expect(windowIsEmpty(restarted)).toBe(true);
    expect(restarted.resumedFrom).toBeNull();
    expect(restarted.restartedFromStaleCursor).toBe(true);
    expect(restarted.filterKey).toBe("q=x");
  });
});

describe("reconciling a committed state", () => {
  const window = appendCatalogPage(
    startWindow("q=x"),
    page([item(1), item(2), item(3)]),
  );

  it("replaces one card's state without moving it", () => {
    const next = replaceMovieState(window, 2, { ...movieState, movie_id: 2 });

    expect(next.items.map((entry) => entry.movie_id)).toEqual([1, 2, 3]);
    expect(next.items[1].state).toMatchObject({ movie_id: 2, revision: 3 });
    expect(next.items[0].state).toBeNull();
  });

  it("returns the same window when the movie is not on screen", () => {
    expect(replaceMovieState(window, 99, movieState)).toBe(window);
  });
});
