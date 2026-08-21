import { describe, expect, it, vi } from "vitest";

import type { CatalogItem } from "@/lib/api";
import {
  browseSnapshotKey,
  readBrowseSnapshot,
  saveBrowseSnapshot,
  snapshotFromWindow,
  SNAPSHOT_MAX_ITEMS,
  SNAPSHOT_TTL_MS,
  windowFromSnapshot,
  type SessionStore,
} from "@/lib/browse/restoration";
import { appendCatalogPage, startWindow } from "@/lib/browse/window";
import { catalogResponse } from "./resource-fixtures";

const NOW = 1_800_000_000_000;
const template = catalogResponse.items[0];

function memoryStore(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  const store: SessionStore & { values: Map<string, string> } = {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  };
  return store;
}

function items(count: number): CatalogItem[] {
  return Array.from({ length: count }, (_, index) => ({
    ...template,
    movie_id: index + 1,
    title: `Title ${index + 1}`,
  }));
}

function windowOf(count: number, filterKey = "q=burning") {
  return appendCatalogPage(startWindow(filterKey), {
    ...catalogResponse,
    items: items(count),
    page: { has_more: true, next_cursor: "next-cursor" },
  });
}

describe("saving and restoring a Browse position", () => {
  it("round-trips the window, the cursor, and the scroll offset", () => {
    const store = memoryStore();
    const key = browseSnapshotKey(900000101, "q=burning");

    saveBrowseSnapshot(
      store,
      key,
      snapshotFromWindow(windowOf(3), { scrollY: 1240.6, now: NOW }),
    );
    const restored = readBrowseSnapshot(store, key, "q=burning", NOW + 1_000);

    expect(restored).toMatchObject({
      filterKey: "q=burning",
      nextCursor: "next-cursor",
      hasMore: true,
      scrollY: 1241,
    });
    expect(restored?.items).toHaveLength(3);
    expect(windowFromSnapshot(restored!)).toMatchObject({
      filterKey: "q=burning",
      hasMore: true,
      nextCursor: "next-cursor",
    });
  });

  it("refuses a snapshot belonging to another filter set", () => {
    const store = memoryStore();
    const key = browseSnapshotKey(900000101, "q=burning");
    saveBrowseSnapshot(
      store,
      key,
      snapshotFromWindow(windowOf(2), { scrollY: 0, now: NOW }),
    );

    expect(readBrowseSnapshot(store, key, "q=other", NOW)).toBeNull();
  });

  it("keys per persona so one persona cannot restore another's window", () => {
    expect(browseSnapshotKey(1, "q=x")).not.toBe(browseSnapshotKey(2, "q=x"));
  });

  it("expires rather than restoring a window from another sitting", () => {
    const store = memoryStore();
    const key = browseSnapshotKey(900000101, "q=burning");
    saveBrowseSnapshot(
      store,
      key,
      snapshotFromWindow(windowOf(2), { scrollY: 40, now: NOW }),
    );

    expect(readBrowseSnapshot(store, key, "q=burning", NOW + SNAPSHOT_TTL_MS - 1)).not.toBeNull();
    expect(readBrowseSnapshot(store, key, "q=burning", NOW + SNAPSHOT_TTL_MS + 1)).toBeNull();
  });

  it("caps what it stores so a deep window cannot fill the tab's storage", () => {
    const snapshot = snapshotFromWindow(windowOf(SNAPSHOT_MAX_ITEMS + 40), {
      scrollY: 0,
      now: NOW,
    });

    expect(snapshot.items).toHaveLength(SNAPSHOT_MAX_ITEMS);
  });

  it("degrades to a normal load when the entry is unusable", () => {
    const key = browseSnapshotKey(900000101, "q=burning");

    expect(readBrowseSnapshot(memoryStore({ [key]: "{" }), key, "q=burning", NOW)).toBeNull();
    expect(
      readBrowseSnapshot(memoryStore({ [key]: '{"version":9}' }), key, "q=burning", NOW),
    ).toBeNull();
    expect(
      readBrowseSnapshot(
        memoryStore({
          [key]: JSON.stringify({
            version: 1,
            filterKey: "q=burning",
            items: [{ movie_id: "not-a-number" }],
            nextCursor: null,
            hasMore: false,
            resumedFrom: null,
            scrollY: 0,
            savedAt: NOW,
          }),
        }),
        key,
        "q=burning",
        NOW,
      ),
    ).toBeNull();
  });

  it("never throws when the store refuses the write", () => {
    const store = memoryStore();
    store.setItem = vi.fn(() => {
      throw new DOMException("QuotaExceededError");
    });

    expect(
      saveBrowseSnapshot(
        store,
        "key",
        snapshotFromWindow(windowOf(2), { scrollY: 0, now: NOW }),
      ),
    ).toBe(false);
  });

  it("ignores an empty window rather than restoring a blank grid", () => {
    const store = memoryStore();
    const key = browseSnapshotKey(900000101, "q=burning");
    saveBrowseSnapshot(
      store,
      key,
      snapshotFromWindow(startWindow("q=burning"), { scrollY: 0, now: NOW }),
    );

    expect(readBrowseSnapshot(store, key, "q=burning", NOW)).toBeNull();
  });
});
