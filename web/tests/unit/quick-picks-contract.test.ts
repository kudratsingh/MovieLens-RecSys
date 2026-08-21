import { describe, expect, it } from "vitest";

import type { MovieState } from "@/lib/api";
import {
  FALLBACK_POLICY_COPY,
  InvalidQuickPickRequestError,
  LEARNED_POLICY_COPY,
  policyHeadline,
  QUICK_PICK_SEMANTICS,
  quickPickHttpRequest,
  quickPickProgress,
  toQuickPickQueue,
  type QuickPickActionKind,
} from "@/lib/quick-picks/contract";
import {
  initialQuickPickState,
  quickPicksReducer,
  type QuickPickState,
} from "@/lib/quick-picks/machine";
import {
  fixtureFallbackPolicy,
  fixtureLearnedPolicy,
  fixtureQuickPickResponse,
} from "@/lib/quick-picks/fixtures";

import { fallbackServingPolicy, learnedServingPolicy } from "./resource-fixtures";

const response = fixtureQuickPickResponse();

function committedState(overrides: Partial<MovieState> = {}): MovieState {
  return {
    dismissed_at: null,
    movie_id: response.items[0].movie_id,
    rating: null,
    rating_updated_at: null,
    revision: 1,
    tenant_id: "demo",
    updated_at: "2026-08-21T12:00:00Z",
    user_id: 900000101,
    watched_at: null,
    watchlisted_at: null,
    ...overrides,
  };
}

function decide(
  action: QuickPickActionKind,
  committed: MovieState,
  rating: number | null = null,
): QuickPickState {
  const start = initialQuickPickState({
    cards: toQuickPickQueue(response),
    policy: fixtureFallbackPolicy,
    requestId: "req-1",
  });
  const requested = quickPicksReducer(start, {
    type: "action-requested",
    action,
    input: "button",
    rating,
  });
  return quickPicksReducer(requested, { type: "commit-succeeded", state: committed });
}

describe("action-to-model semantics", () => {
  it("keeps watchlist organizational: no positive progress and no exclusion", () => {
    const semantics = QUICK_PICK_SEMANTICS.watchlist;
    expect(semantics.advancesPositiveProgress).toBe(false);
    expect(semantics.excludesFromServing).toBe(false);
    expect(semantics.undoable).toBe(false);

    const after = decide("watchlist", committedState({ watchlisted_at: "2026-08-21T12:00:00Z" }));
    expect(after.committedPositiveSignals).toBe(0);
    expect(after.undo).toBeNull();
  });

  it("advances progress for watched only after the canonical state confirms it", () => {
    expect(QUICK_PICK_SEMANTICS.watched.advancesPositiveProgress).toBe(true);

    const confirmed = decide("watched", committedState({ watched_at: "2026-08-21T12:00:00Z" }));
    expect(confirmed.committedPositiveSignals).toBe(1);

    // A 200 that somehow carries no watched timestamp is not a positive signal.
    const unconfirmed = decide("watched", committedState({ watched_at: null }));
    expect(unconfirmed.committedPositiveSignals).toBe(0);
  });

  it("dismisses without positive progress and keeps the decision undoable", () => {
    const semantics = QUICK_PICK_SEMANTICS.dismiss;
    expect(semantics.advancesPositiveProgress).toBe(false);
    expect(semantics.excludesFromServing).toBe(true);
    expect(semantics.undoable).toBe(true);
    expect(semantics.modelEffect).toContain("never becomes a negative training label");

    const after = decide("dismiss", committedState({ dismissed_at: "2026-08-21T12:00:00Z", revision: 4 }));
    expect(after.committedPositiveSignals).toBe(0);
    expect(after.undo).toEqual({
      card: toQuickPickQueue(response)[0],
      revision: 4,
    });
  });

  it("treats rating magnitude as display feedback only", () => {
    const outcomes = [1, 2, 3, 4, 5].map((rating) => {
      const state = decide(
        "watched",
        committedState({ rating, watched_at: "2026-08-21T12:00:00Z" }),
        rating,
      );
      return {
        rating,
        positive: state.committedPositiveSignals,
        request: quickPickHttpRequest({
          action: "watched",
          movieId: 105,
          rating,
          expectedRevision: null,
        }),
      };
    });

    // Every star produces the same semantics; only the transmitted value moves.
    expect(new Set(outcomes.map((outcome) => outcome.positive))).toEqual(new Set([1]));
    expect(new Set(outcomes.map((outcome) => outcome.request.resource))).toEqual(
      new Set(["rating"]),
    );
    expect(outcomes.map((outcome) => outcome.request.body)).toEqual(
      [1, 2, 3, 4, 5].map((rating) => ({ rating })),
    );
  });
});

describe("action-to-request mapping", () => {
  it("routes each decision to the resource that carries its meaning", () => {
    expect(
      quickPickHttpRequest({ action: "watchlist", movieId: 1, rating: null, expectedRevision: null }),
    ).toEqual({ resource: "watchlist", method: "PUT", body: null, expectedRevision: null });

    expect(
      quickPickHttpRequest({ action: "watched", movieId: 1, rating: null, expectedRevision: null }),
    ).toEqual({ resource: "watched", method: "PUT", body: null, expectedRevision: null });

    expect(
      quickPickHttpRequest({ action: "dismiss", movieId: 1, rating: null, expectedRevision: null }),
    ).toEqual({ resource: "dismissal", method: "PUT", body: null, expectedRevision: null });

    expect(
      quickPickHttpRequest({ action: "undo-dismiss", movieId: 1, rating: null, expectedRevision: 4 }),
    ).toEqual({ resource: "dismissal", method: "DELETE", body: null, expectedRevision: 4 });
  });

  it("refuses a rating the API and database would reject", () => {
    expect(() =>
      quickPickHttpRequest({ action: "watched", movieId: 1, rating: 4.2, expectedRevision: null }),
    ).toThrowError(InvalidQuickPickRequestError);
    expect(() =>
      quickPickHttpRequest({ action: "watched", movieId: 1, rating: 0, expectedRevision: null }),
    ).toThrowError(InvalidQuickPickRequestError);
  });
});

describe("progress and policy copy", () => {
  it("adds locally committed signals to the count the policy last reported", () => {
    expect(quickPickProgress(fallbackServingPolicy, 0)).toMatchObject({
      count: 2,
      learned: false,
      remaining: 3,
      thresholdReached: false,
    });
    expect(quickPickProgress(fallbackServingPolicy, 3)).toMatchObject({
      count: 5,
      learned: false,
      remaining: 0,
      thresholdReached: true,
    });
  });

  it("never reports learned serving from a count alone", () => {
    const atThreshold = quickPickProgress(fixtureFallbackPolicy, 3);
    expect(atThreshold.thresholdReached).toBe(true);
    expect(atThreshold.learned).toBe(false);
    expect(policyHeadline(fixtureFallbackPolicy)).toBe(FALLBACK_POLICY_COPY);
  });

  it("branches copy on the learned flag rather than the policy name", () => {
    expect(policyHeadline(fixtureLearnedPolicy)).toBe(LEARNED_POLICY_COPY);
    expect(policyHeadline(learnedServingPolicy)).toBe(LEARNED_POLICY_COPY);
    // Two different deployments emit two different names for learned serving.
    expect(fixtureLearnedPolicy.name).not.toBe(learnedServingPolicy.name);
  });
});
