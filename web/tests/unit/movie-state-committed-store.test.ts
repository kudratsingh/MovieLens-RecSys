import { describe, expect, it } from "vitest";

import type { MovieState } from "@/lib/api";
import {
  applyCommittedStates,
  COMMITTED_STATE_MAX_ENTRIES,
  COMMITTED_STATE_TTL_MS,
  readCommittedStates,
  recordCommittedState,
  type SessionStore,
} from "@/lib/movie-state/committed-store";
import { movieState } from "./resource-fixtures";

const NOW = 1_800_000_000_000;
const USER_ID = 900000101;

function memoryStore(): SessionStore {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  };
}

function state(movieId: number, revision: number): MovieState {
  return { ...movieState, movie_id: movieId, revision };
}

describe("relaying committed state between routes", () => {
  it("returns what was recorded for this persona", () => {
    const store = memoryStore();
    recordCommittedState(store, USER_ID, state(101, 4), NOW);
    recordCommittedState(store, USER_ID, state(102, 1), NOW);

    const relayed = readCommittedStates(store, USER_ID, NOW);
    expect(relayed.get(101)).toMatchObject({ movie_id: 101, revision: 4 });
    expect(relayed.get(102)).toMatchObject({ movie_id: 102, revision: 1 });
    expect(readCommittedStates(store, 999, NOW).size).toBe(0);
  });

  it("keeps the latest write for a movie", () => {
    const store = memoryStore();
    recordCommittedState(store, USER_ID, state(101, 2), NOW);
    recordCommittedState(store, USER_ID, state(101, 3), NOW + 10);

    expect(readCommittedStates(store, USER_ID, NOW + 20).get(101)?.revision).toBe(3);
  });

  it("expires entries rather than resurrecting an old echo", () => {
    const store = memoryStore();
    recordCommittedState(store, USER_ID, state(101, 2), NOW);

    expect(readCommittedStates(store, USER_ID, NOW + COMMITTED_STATE_TTL_MS + 1).size).toBe(0);
  });

  it("keeps the relay bounded", () => {
    const store = memoryStore();
    for (let index = 0; index < COMMITTED_STATE_MAX_ENTRIES + 25; index += 1) {
      recordCommittedState(store, USER_ID, state(index + 1, 1), NOW + index);
    }

    expect(readCommittedStates(store, USER_ID, NOW + 1_000).size).toBe(
      COMMITTED_STATE_MAX_ENTRIES,
    );
  });

  it("survives an unreadable entry without throwing", () => {
    const store = memoryStore();
    store.setItem("movielens:committed-state:900000101", "not json");

    expect(readCommittedStates(store, USER_ID, NOW).size).toBe(0);
  });
});

describe("folding relayed state into catalog items", () => {
  const items = [
    { movie_id: 101, state: null },
    { movie_id: 102, state: state(102, 5) },
    { movie_id: 103, state: state(103, 1) },
  ];

  it("fills in a state the grid never had", () => {
    const relayed = new Map([[101, state(101, 1)]]);
    expect(applyCommittedStates(items, relayed)[0].state).toMatchObject({
      movie_id: 101,
      revision: 1,
    });
  });

  it("never overwrites a fresher server read with an older echo", () => {
    const relayed = new Map([[102, state(102, 3)]]);
    expect(applyCommittedStates(items, relayed)[1].state?.revision).toBe(5);
  });

  it("adopts a newer revision", () => {
    const relayed = new Map([[103, state(103, 6)]]);
    expect(applyCommittedStates(items, relayed)[2].state?.revision).toBe(6);
  });

  it("leaves untouched items exactly as they were", () => {
    const result = applyCommittedStates(items, new Map());
    expect(result[0]).toBe(items[0]);
  });
});
