import { describe, expect, it } from "vitest";

import {
  markNudgeAnswered,
  nudgeEarnedBy,
  readSkipRecord,
  recordWatchlistSkip,
  skipCounterKey,
} from "@/lib/discover/skip-counter";
import type { SessionStore } from "@/lib/movie-state/committed-store";

/** A store that behaves; the awkward ones are built per test below. */
function fakeStore(seed: Record<string, string> = {}): SessionStore & {
  entries: Record<string, string>;
} {
  const entries = { ...seed };
  return {
    entries,
    getItem: (key: string) => entries[key] ?? null,
    setItem: (key: string, value: string) => {
      entries[key] = value;
    },
    removeItem: (key: string) => {
      delete entries[key];
    },
  };
}

const PERSONA = 900000102;

describe("counting the skips that earn the question", () => {
  it("starts at nothing and counts up", () => {
    const store = fakeStore();

    expect(readSkipRecord(store, PERSONA)).toEqual({ skips: 0, answered: false });
    expect(recordWatchlistSkip(store, PERSONA).skips).toBe(1);
    expect(recordWatchlistSkip(store, PERSONA).skips).toBe(2);
    expect(readSkipRecord(store, PERSONA)).toEqual({ skips: 2, answered: false });
  });

  it("counts each persona separately", () => {
    // The personas are different viewers. Skipping three of Drama Fan's saved
    // titles says nothing about what Action Fan wants featured.
    const store = fakeStore();
    recordWatchlistSkip(store, PERSONA);
    recordWatchlistSkip(store, PERSONA);

    expect(readSkipRecord(store, 900000101).skips).toBe(0);
    expect(store.entries[skipCounterKey(PERSONA)]).toBeDefined();
  });

  it("earns the question exactly at the threshold and never after it", () => {
    const store = fakeStore();

    expect(nudgeEarnedBy(recordWatchlistSkip(store, PERSONA))).toBe(false);
    expect(nudgeEarnedBy(recordWatchlistSkip(store, PERSONA))).toBe(false);
    expect(nudgeEarnedBy(recordWatchlistSkip(store, PERSONA))).toBe(true);
    // A viewer who keeps skipping past the offer is answering by not answering.
    expect(nudgeEarnedBy(recordWatchlistSkip(store, PERSONA))).toBe(false);
  });

  it("stops asking once the viewer has answered, whichever way", () => {
    const store = fakeStore();
    recordWatchlistSkip(store, PERSONA);
    recordWatchlistSkip(store, PERSONA);
    markNudgeAnswered(store, PERSONA);

    const third = recordWatchlistSkip(store, PERSONA);
    expect(third.skips).toBe(3);
    expect(third.answered).toBe(true);
    expect(nudgeEarnedBy(third)).toBe(false);
  });
});

describe("a counter is never worth a failure", () => {
  it("reads nothing from an absent store", () => {
    expect(readSkipRecord(null, PERSONA)).toEqual({ skips: 0, answered: false });
    expect(recordWatchlistSkip(null, PERSONA)).toEqual({ skips: 1, answered: false });
  });

  it("survives a store that throws on read and on write", () => {
    // A private window, a storage-disabled browser, a quota error: the cost is
    // the nudge, which is an offer, never a mechanism.
    const hostile: SessionStore = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => {},
    };

    expect(readSkipRecord(hostile, PERSONA)).toEqual({ skips: 0, answered: false });
    expect(() => recordWatchlistSkip(hostile, PERSONA)).not.toThrow();
  });

  it("ignores anything stored that is not a record it wrote", () => {
    for (const raw of ["not json", "[]", '{"skips":-4}', '{"skips":"3"}', "null"]) {
      const store = fakeStore({ [skipCounterKey(PERSONA)]: raw });
      expect(readSkipRecord(store, PERSONA).skips).toBe(0);
    }
  });
});
