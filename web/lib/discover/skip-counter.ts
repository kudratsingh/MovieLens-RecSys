/**
 * How many watchlisted titles this viewer has skipped in this tab, per persona.
 *
 * Session storage rather than the API, deliberately. The count is a reason to
 * *ask a question once*, not a fact about the persona: it should not follow a
 * reviewer from one browser to another, it should not survive the tab, and it
 * should never become a second signal store next to `user_movie_state`. What
 * the viewer answers is durable — that is the preference, and it goes to the
 * API.
 *
 * Per persona because the personas are different viewers. Skipping three of
 * Drama Fan's watchlisted titles says nothing about Action Fan.
 *
 * Every access is wrapped: a private window, a storage-disabled browser, or a
 * quota error costs at most the nudge, which is an offer rather than a
 * mechanism.
 */

import type { SessionStore } from "@/lib/movie-state/committed-store";
import { WATCHLIST_SKIPS_BEFORE_NUDGE } from "@/lib/discover/featured-preference";

export type SkipRecord = {
  /** Watchlisted titles passed over in this tab, for this persona. */
  skips: number;
  /** The viewer answered the nudge — either way — so it is not asked again. */
  answered: boolean;
};

const EMPTY: SkipRecord = { skips: 0, answered: false };

export function skipCounterKey(userId: number): string {
  return `movielens:watchlist-skips:${userId}`;
}

export function readSkipRecord(
  store: SessionStore | null,
  userId: number,
): SkipRecord {
  if (!store) return EMPTY;
  let raw: string | null;
  try {
    raw = store.getItem(skipCounterKey(userId));
  } catch {
    return EMPTY;
  }
  if (!raw) return EMPTY;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return EMPTY;
    }
    const value = parsed as { skips?: unknown; answered?: unknown };
    return {
      skips:
        typeof value.skips === "number" && Number.isSafeInteger(value.skips) && value.skips >= 0
          ? value.skips
          : 0,
      answered: value.answered === true,
    };
  } catch {
    return EMPTY;
  }
}

function write(store: SessionStore | null, userId: number, record: SkipRecord): SkipRecord {
  if (!store) return record;
  try {
    store.setItem(skipCounterKey(userId), JSON.stringify(record));
  } catch {
    // Losing the counter costs a nudge, never a decision.
  }
  return record;
}

/** Records one skip and returns the record it produced. */
export function recordWatchlistSkip(
  store: SessionStore | null,
  userId: number,
): SkipRecord {
  const current = readSkipRecord(store, userId);
  return write(store, userId, { ...current, skips: current.skips + 1 });
}

/** Marks the question asked and answered, whichever way the viewer answered. */
export function markNudgeAnswered(
  store: SessionStore | null,
  userId: number,
): SkipRecord {
  const current = readSkipRecord(store, userId);
  return write(store, userId, { ...current, answered: true });
}

/**
 * Whether *this* skip is the one that earns the question.
 *
 * Strict equality, not `>=`: the offer is made once, at the threshold, and a
 * viewer who keeps skipping past it is answering by not answering. The
 * `answered` flag covers the other direction — an explicit answer at any point
 * settles it for the rest of the session, reload included.
 */
export function nudgeEarnedBy(record: SkipRecord): boolean {
  return !record.answered && record.skips === WATCHLIST_SKIPS_BEFORE_NUDGE;
}
