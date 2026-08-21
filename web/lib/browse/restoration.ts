/**
 * Session-local Browse position, so a detail visit is not a one-way trip.
 *
 * Cursor pagination cannot be replayed from a URL — page four is only
 * reachable by having asked for pages one through three — so returning to a
 * scrolled, four-pages-deep grid means keeping the window somewhere. It is
 * kept in `sessionStorage`, per tab, keyed by the filter set it belongs to, so
 * a different query can never restore another query's results.
 *
 * The entry is written when the viewer leaves for a movie and read back when
 * Browse mounts against the same filter key inside the TTL. That window is
 * deliberately generous — someone can read a synopsis for a while — and
 * bounded, because a restored grid is a snapshot of durable state, not a fresh
 * read of it.
 *
 * Everything here is device-local data we wrote ourselves, but it is still
 * validated on the way back in: another tab, an extension, or a released build
 * with a different shape can all put something unexpected in that slot, and a
 * malformed entry must degrade to a normal first-page load.
 */

import type { CatalogItem } from "@/lib/api";
import { isCatalogItem } from "@/lib/resources/validate";
import { startWindow, type CatalogWindow } from "@/lib/browse/window";

export const SNAPSHOT_VERSION = 1;
export const SNAPSHOT_TTL_MS = 30 * 60 * 1_000;
/** Eight pages at the endpoint's default size; past that, reload is cheaper. */
export const SNAPSHOT_MAX_ITEMS = 192;

export type BrowseSnapshot = {
  version: typeof SNAPSHOT_VERSION;
  filterKey: string;
  items: CatalogItem[];
  nextCursor: string | null;
  hasMore: boolean;
  resumedFrom: string | null;
  scrollY: number;
  savedAt: number;
};

/** The slice of `Storage` used here, so tests can pass a plain object. */
export type SessionStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function browseSnapshotKey(userId: number, filterKey: string): string {
  return `movielens:browse:${userId}:${filterKey}`;
}

export function snapshotFromWindow(
  window: CatalogWindow,
  options: { scrollY: number; now?: number },
): BrowseSnapshot {
  return {
    version: SNAPSHOT_VERSION,
    filterKey: window.filterKey,
    // Keep the head of the window: it is the part the viewer scrolled through.
    items: window.items.slice(0, SNAPSHOT_MAX_ITEMS),
    nextCursor: window.nextCursor,
    hasMore: window.hasMore,
    resumedFrom: window.resumedFrom,
    scrollY: Math.max(0, Math.round(options.scrollY)),
    savedAt: options.now ?? Date.now(),
  };
}

export function windowFromSnapshot(snapshot: BrowseSnapshot): CatalogWindow {
  return {
    ...startWindow(snapshot.filterKey, snapshot.resumedFrom),
    items: snapshot.items,
    nextCursor: snapshot.nextCursor,
    hasMore: snapshot.hasMore && Boolean(snapshot.nextCursor),
    pagesLoaded: Math.max(1, Math.ceil(snapshot.items.length / 24)),
  };
}

export function saveBrowseSnapshot(
  store: SessionStore,
  key: string,
  snapshot: BrowseSnapshot,
): boolean {
  try {
    store.setItem(key, JSON.stringify(snapshot));
    return true;
  } catch {
    // A full or unavailable store costs the viewer their scroll position, not
    // their session. Losing restoration is never worth throwing over.
    return false;
  }
}

function isSnapshot(value: unknown, filterKey: string): value is BrowseSnapshot {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<BrowseSnapshot>;
  return (
    candidate.version === SNAPSHOT_VERSION &&
    candidate.filterKey === filterKey &&
    Array.isArray(candidate.items) &&
    candidate.items.every(isCatalogItem) &&
    (candidate.nextCursor === null || typeof candidate.nextCursor === "string") &&
    (candidate.resumedFrom === null || typeof candidate.resumedFrom === "string") &&
    typeof candidate.hasMore === "boolean" &&
    typeof candidate.scrollY === "number" &&
    Number.isFinite(candidate.scrollY) &&
    typeof candidate.savedAt === "number" &&
    Number.isFinite(candidate.savedAt)
  );
}

export function readBrowseSnapshot(
  store: SessionStore,
  key: string,
  filterKey: string,
  now: number = Date.now(),
): BrowseSnapshot | null {
  let raw: string | null;
  try {
    raw = store.getItem(key);
  } catch {
    return null;
  }
  if (!raw) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isSnapshot(parsed, filterKey)) return null;
  if (parsed.items.length === 0) return null;
  if (now - parsed.savedAt > SNAPSHOT_TTL_MS || parsed.savedAt > now) return null;
  return parsed;
}
