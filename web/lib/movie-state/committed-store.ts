/**
 * A tab-local relay for movie state that has already been committed.
 *
 * A restored Browse window is a snapshot taken before the viewer opened a
 * movie, so a watchlist action taken on the detail page would otherwise be
 * invisible on the way back — the grid would contradict a change the viewer
 * just watched succeed. Rather than discard restoration or refetch the whole
 * window, the detail route records each committed canonical state here and
 * Browse folds it into the snapshot it restores.
 *
 * Only responses the API actually committed are recorded, revision included,
 * so this stays a cache of canonical answers and never becomes a second,
 * client-side source of truth. It is per tab, per persona, capped, and
 * expiring; nothing here is authoritative and a fresh read always wins.
 */

import type { MovieState } from "@/lib/api";
import { isMovieState } from "@/lib/resources/validate";

export const COMMITTED_STATE_TTL_MS = 30 * 60 * 1_000;
export const COMMITTED_STATE_MAX_ENTRIES = 120;

export type SessionStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

type CommittedEntry = { state: MovieState; savedAt: number };
type CommittedMap = Record<string, CommittedEntry>;

export function committedStateKey(userId: number): string {
  return `movielens:committed-state:${userId}`;
}

function readMap(store: SessionStore, key: string): CommittedMap {
  let raw: string | null;
  try {
    raw = store.getItem(key);
  } catch {
    return {};
  }
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return {};
    }
    const entries = Object.entries(parsed as Record<string, unknown>).filter(
      (entry): entry is [string, CommittedEntry] => {
        const value = entry[1];
        return (
          typeof value === "object" &&
          value !== null &&
          isMovieState((value as CommittedEntry).state) &&
          typeof (value as CommittedEntry).savedAt === "number"
        );
      },
    );
    return Object.fromEntries(entries);
  } catch {
    return {};
  }
}

export function recordCommittedState(
  store: SessionStore,
  userId: number,
  state: MovieState,
  now: number = Date.now(),
): void {
  const key = committedStateKey(userId);
  const current = readMap(store, key);
  current[String(state.movie_id)] = { state, savedAt: now };

  const fresh = Object.entries(current)
    .filter(([, entry]) => now - entry.savedAt <= COMMITTED_STATE_TTL_MS)
    .sort(([, left], [, right]) => right.savedAt - left.savedAt)
    .slice(0, COMMITTED_STATE_MAX_ENTRIES);

  try {
    store.setItem(key, JSON.stringify(Object.fromEntries(fresh)));
  } catch {
    // Losing the relay only costs a stale card until the next read.
  }
}

export function readCommittedStates(
  store: SessionStore,
  userId: number,
  now: number = Date.now(),
): Map<number, MovieState> {
  const states = new Map<number, MovieState>();
  for (const [movieId, entry] of Object.entries(
    readMap(store, committedStateKey(userId)),
  )) {
    if (now - entry.savedAt > COMMITTED_STATE_TTL_MS) continue;
    if (!/^\d+$/.test(movieId)) continue;
    states.set(Number(movieId), entry.state);
  }
  return states;
}

/**
 * Folds relayed states into a list of catalog items. A relayed entry only wins
 * when it is strictly newer than what the item carries, so a fresher server
 * read is never overwritten by an older local echo.
 */
export function applyCommittedStates<
  T extends { movie_id: number; state: MovieState | null },
>(items: readonly T[], states: Map<number, MovieState>): T[] {
  return items.map((item) => {
    const relayed = states.get(item.movie_id);
    if (!relayed) return item;
    if (item.state && item.state.revision >= relayed.revision) return item;
    return { ...item, state: relayed };
  });
}
