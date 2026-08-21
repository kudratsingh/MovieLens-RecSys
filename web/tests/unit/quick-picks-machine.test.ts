import { describe, expect, it } from "vitest";

import type { MovieState, ServingPolicy } from "@/lib/api";
import { toQuickPickQueue, type QuickPickActionKind } from "@/lib/quick-picks/contract";
import {
  fixtureFallbackPolicy,
  fixtureQuickPickResponse,
} from "@/lib/quick-picks/fixtures";
import {
  actionFocusId,
  canUndo,
  cardMotion,
  currentCard,
  initialQuickPickState,
  isBusy,
  PRIMARY_ACTION_FOCUS_ID,
  progressOf,
  quickPicksReducer,
  REFRESH_AFTER_POSITIVE_SIGNALS,
  resolveKeyboardAction,
  resolveSwipe,
  SWIPE_DISTANCE_PX,
  type QuickPickEvent,
  type QuickPickInput,
  type QuickPickState,
} from "@/lib/quick-picks/machine";

const response = fixtureQuickPickResponse();
const cards = toQuickPickQueue(response);

function start(
  options: { policy?: ServingPolicy; cardCount?: number; requestId?: string } = {},
): QuickPickState {
  return initialQuickPickState({
    cards: cards.slice(0, options.cardCount ?? cards.length),
    policy: options.policy ?? fixtureFallbackPolicy,
    requestId: options.requestId ?? "req-initial",
  });
}

function run(state: QuickPickState, events: QuickPickEvent[]): QuickPickState {
  return events.reduce(quickPicksReducer, state);
}

function committed(overrides: Partial<MovieState> = {}): MovieState {
  return {
    dismissed_at: null,
    movie_id: cards[0].movieId,
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

const watched = (movieId: number) =>
  committed({ movie_id: movieId, watched_at: "2026-08-21T12:00:00Z" });
const dismissed = (movieId: number, revision = 1) =>
  committed({ movie_id: movieId, dismissed_at: "2026-08-21T12:00:00Z", revision });

function decideAll(
  state: QuickPickState,
  decisions: readonly { action: QuickPickActionKind; state: MovieState }[],
): QuickPickState {
  return decisions.reduce(
    (current, decision) =>
      run(current, [
        { type: "action-requested", action: decision.action, input: "button" },
        { type: "commit-succeeded", state: decision.state },
      ]),
    state,
  );
}

describe("input parity", () => {
  const inputs: QuickPickInput[] = ["button", "keyboard", "gesture"];

  it("produces the same state from a button, a key, and a swipe", () => {
    const outcomes = inputs.map((input) => {
      const after = run(start(), [
        { type: "action-requested", action: "dismiss", input },
        { type: "commit-succeeded", state: dismissed(cards[0].movieId, 3) },
      ]);
      // The recorded input is the only field allowed to differ, and it is
      // consumed before the commit lands.
      return {
        queue: after.queue.map((card) => card.movieId),
        undo: after.undo?.card.movieId,
        progress: progressOf(after).count,
        announcement: after.announcement,
      };
    });

    expect(outcomes[1]).toEqual(outcomes[0]);
    expect(outcomes[2]).toEqual(outcomes[0]);
  });

  it("maps keys and swipes onto the same four decisions", () => {
    expect(resolveKeyboardAction({ key: "j" })).toBe("dismiss");
    expect(resolveKeyboardAction({ key: "K" })).toBe("watchlist");
    expect(resolveKeyboardAction({ key: "l" })).toBe("watched");
    expect(resolveKeyboardAction({ key: "u" })).toBe("undo-dismiss");

    expect(resolveSwipe({ dx: -SWIPE_DISTANCE_PX, dy: 4, elapsedMs: 220 })).toBe("dismiss");
    expect(resolveSwipe({ dx: SWIPE_DISTANCE_PX, dy: -4, elapsedMs: 220 })).toBe("watchlist");
    expect(resolveSwipe({ dx: 2, dy: -SWIPE_DISTANCE_PX, elapsedMs: 220 })).toBe("watched");
  });

  it("ignores keys that belong to the page and swipes that are not deliberate", () => {
    expect(resolveKeyboardAction({ key: "l", metaKey: true })).toBeNull();
    expect(resolveKeyboardAction({ key: "j", inField: true })).toBeNull();
    expect(resolveKeyboardAction({ key: "Enter" })).toBeNull();

    expect(resolveSwipe({ dx: -20, dy: 0, elapsedMs: 100 })).toBeNull();
    expect(resolveSwipe({ dx: 0, dy: SWIPE_DISTANCE_PX, elapsedMs: 100 })).toBeNull();
    expect(resolveSwipe({ dx: -200, dy: 0, elapsedMs: 4_000 })).toBeNull();
  });
});

describe("committing a decision", () => {
  it("keeps the card in place while the write is in flight", () => {
    const pending = quickPicksReducer(start(), {
      type: "action-requested",
      action: "watched",
      input: "button",
    });

    expect(isBusy(pending)).toBe(true);
    expect(currentCard(pending)?.movieId).toBe(cards[0].movieId);
    expect(pending.pending).toMatchObject({
      action: "watched",
      movieId: cards[0].movieId,
      // No revision was ever observed for a queue card, so none is asserted.
      expectedRevision: null,
    });
  });

  it("refuses a second decision until the first settles", () => {
    const pending = quickPicksReducer(start(), {
      type: "action-requested",
      action: "watched",
      input: "button",
    });
    const again = quickPicksReducer(pending, {
      type: "action-requested",
      action: "dismiss",
      input: "keyboard",
    });

    expect(again).toBe(pending);
  });

  it("advances only after the canonical state arrives", () => {
    const after = run(start(), [
      { type: "action-requested", action: "watched", input: "button" },
      { type: "commit-succeeded", state: watched(cards[0].movieId) },
    ]);

    expect(currentCard(after)?.movieId).toBe(cards[1].movieId);
    expect(progressOf(after).count).toBe(fixtureFallbackPolicy.positive_signal_count + 1);
    expect(after.announcement).toContain("3 of 5 watched signals recorded");
    expect(after.focusRequest).toBe(actionFocusId("watched"));
  });

  it("restores the card, the controls, and focus when the write fails", () => {
    const after = run(start(), [
      { type: "action-requested", action: "dismiss", input: "gesture" },
      { type: "commit-failed", message: "The recommendation API returned 503." },
    ]);

    expect(currentCard(after)?.movieId).toBe(cards[0].movieId);
    expect(after.queue).toHaveLength(cards.length);
    expect(isBusy(after)).toBe(false);
    expect(after.error).toContain("503");
    expect(after.announcement).toContain("The card is unchanged.");
    expect(after.focusRequest).toBe(actionFocusId("dismiss"));
    expect(progressOf(after).count).toBe(fixtureFallbackPolicy.positive_signal_count);
    expect(after.undo).toBeNull();
  });

  it("lets the same decision be retried after a failure", () => {
    const failed = run(start(), [
      { type: "action-requested", action: "watched", input: "button" },
      { type: "commit-failed", message: "Timed out." },
    ]);
    const retried = run(failed, [
      { type: "action-requested", action: "watched", input: "button" },
      { type: "commit-succeeded", state: watched(cards[0].movieId) },
    ]);

    expect(retried.error).toBeNull();
    expect(progressOf(retried).count).toBe(fixtureFallbackPolicy.positive_signal_count + 1);
    expect(currentCard(retried)?.movieId).toBe(cards[1].movieId);
  });
});

describe("undo", () => {
  it("offers undo only after a dismissal", () => {
    const afterDismiss = run(start(), [
      { type: "action-requested", action: "dismiss", input: "button" },
      { type: "commit-succeeded", state: dismissed(cards[0].movieId, 2) },
    ]);
    expect(canUndo(afterDismiss)).toBe(true);

    const afterWatchlist = run(start(), [
      { type: "action-requested", action: "watchlist", input: "button" },
      { type: "commit-succeeded", state: committed({ watchlisted_at: "2026-08-21T12:00:00Z" }) },
    ]);
    expect(canUndo(afterWatchlist)).toBe(false);
  });

  it("asserts the revision the dismissal returned", () => {
    const requested = run(start(), [
      { type: "action-requested", action: "dismiss", input: "button" },
      { type: "commit-succeeded", state: dismissed(cards[0].movieId, 7) },
      { type: "action-requested", action: "undo-dismiss", input: "keyboard" },
    ]);

    expect(requested.pending).toMatchObject({
      action: "undo-dismiss",
      movieId: cards[0].movieId,
      expectedRevision: 7,
    });
  });

  it("puts the restored title back at the front of the queue", () => {
    const restored = run(start(), [
      { type: "action-requested", action: "dismiss", input: "button" },
      { type: "commit-succeeded", state: dismissed(cards[0].movieId, 2) },
      { type: "action-requested", action: "undo-dismiss", input: "button" },
      { type: "commit-succeeded", state: committed({ dismissed_at: null, revision: 3 }) },
    ]);

    expect(currentCard(restored)?.movieId).toBe(cards[0].movieId);
    expect(restored.actedMovieIds).not.toContain(cards[0].movieId);
    expect(restored.undo).toBeNull();
    expect(restored.announcement).toContain("back in the queue");
    expect(progressOf(restored).count).toBe(fixtureFallbackPolicy.positive_signal_count);
  });

  it("does nothing when there is no dismissal to undo", () => {
    const state = start();
    expect(quickPicksReducer(state, {
      type: "action-requested",
      action: "undo-dismiss",
      input: "keyboard",
    })).toBe(state);
  });
});

describe("refetch policy", () => {
  it("asks for a fresh queue after the third committed watched signal", () => {
    // Starting from zero so the batch rule fires before the five-signal rule.
    const coldPolicy: ServingPolicy = { ...fixtureFallbackPolicy, positive_signal_count: 0 };
    const twoSignals = decideAll(start({ policy: coldPolicy }), [
      { action: "watched", state: watched(cards[0].movieId) },
      { action: "watched", state: watched(cards[1].movieId) },
    ]);
    expect(twoSignals.refreshRequest).toBeNull();

    const third = decideAll(twoSignals, [
      { action: "watched", state: watched(cards[2].movieId) },
    ]);
    expect(third.positiveSignalsSinceLoad).toBe(REFRESH_AFTER_POSITIVE_SIGNALS);
    expect(third.refreshRequest?.reason).toBe("positive-signal-batch");
    expect(third.status).toBe("refreshing");
  });

  it("does not count dismissals or watchlist saves toward that batch", () => {
    const after = decideAll(start(), [
      { action: "dismiss", state: dismissed(cards[0].movieId) },
      { action: "watchlist", state: committed({ movie_id: cards[1].movieId, watchlisted_at: "2026-08-21T12:00:00Z" }) },
      { action: "dismiss", state: dismissed(cards[2].movieId) },
    ]);

    expect(after.positiveSignalsSinceLoad).toBe(0);
    expect(after.refreshRequest).toBeNull();
  });

  it("refetches at the five-signal boundary instead of announcing a transition", () => {
    const policy: ServingPolicy = { ...fixtureFallbackPolicy, positive_signal_count: 4 };
    const after = decideAll(start({ policy }), [
      { action: "watched", state: watched(cards[0].movieId) },
    ]);

    expect(progressOf(after).thresholdReached).toBe(true);
    expect(progressOf(after).learned).toBe(false);
    expect(after.refreshRequest?.reason).toBe("threshold-reached");
  });

  it("refetches once when the queue runs out and then stops asking", () => {
    const emptied = decideAll(start({ cardCount: 1 }), [
      { action: "dismiss", state: dismissed(cards[0].movieId) },
    ]);
    expect(emptied.refreshRequest?.reason).toBe("exhausted");

    const stillEmpty = quickPicksReducer(emptied, {
      type: "queue-loaded",
      source: { cards: [], policy: fixtureFallbackPolicy, requestId: "req-2" },
    });
    expect(stillEmpty.status).toBe("exhausted");
    expect(stillEmpty.refreshRequest).toBeNull();
    expect(stillEmpty.announcement).toContain("No more picks");
  });

  it("takes the refreshed policy count as authoritative", () => {
    const afterSignals = decideAll(start(), [
      { action: "watched", state: watched(cards[0].movieId) },
    ]);
    expect(afterSignals.committedPositiveSignals).toBe(1);

    const refreshed = quickPicksReducer(afterSignals, {
      type: "queue-loaded",
      source: {
        cards: cards.slice(1),
        policy: { ...fixtureFallbackPolicy, positive_signal_count: 3 },
        requestId: "req-2",
      },
    });

    // 3 from the API, not 3 + the 1 already folded into it.
    expect(progressOf(refreshed).count).toBe(3);
    expect(refreshed.actedMovieIds).toEqual([]);
  });

  it("keeps a usable queue when a refresh fails", () => {
    const refreshing = quickPicksReducer(start(), {
      type: "refresh-requested",
      reason: "manual",
    });
    const failed = quickPicksReducer(refreshing, {
      type: "queue-failed",
      message: "The recommendation API did not answer in time.",
    });

    expect(failed.status).toBe("deciding");
    expect(currentCard(failed)?.movieId).toBe(cards[0].movieId);
    expect(failed.error).toContain("did not answer");
  });

  it("keeps a dismissal undoable across a refresh", () => {
    const dismissedState = decideAll(start(), [
      { action: "dismiss", state: dismissed(cards[0].movieId, 5) },
    ]);
    const refreshed = quickPicksReducer(dismissedState, {
      type: "queue-loaded",
      source: { cards: cards.slice(1), policy: fixtureFallbackPolicy, requestId: "req-2" },
    });

    expect(canUndo(refreshed)).toBe(true);
    expect(refreshed.undo?.revision).toBe(5);
  });
});

describe("queue exhaustion and restart", () => {
  it("reports an empty starting queue as exhausted rather than a broken card", () => {
    const empty = initialQuickPickState({
      cards: [],
      policy: fixtureFallbackPolicy,
      requestId: "req-empty",
    });

    expect(empty.status).toBe("exhausted");
    expect(currentCard(empty)).toBeNull();
    expect(empty.announcement).toBe("");
  });

  it("lets a restart ask again from the exhausted state", () => {
    const empty = initialQuickPickState({
      cards: [],
      policy: fixtureFallbackPolicy,
      requestId: "req-empty",
    });
    const restarted = quickPicksReducer(empty, {
      type: "refresh-requested",
      reason: "manual",
    });

    expect(restarted.refreshRequest?.reason).toBe("manual");
    expect(restarted.status).toBe("refreshing");
  });

  it("announces a fresh queue but stays quiet on the first load", () => {
    expect(start().announcement).toBe("");

    const refreshed = quickPicksReducer(start(), {
      type: "queue-loaded",
      source: { cards: cards.slice(0, 4), policy: fixtureFallbackPolicy, requestId: "req-2" },
    });
    expect(refreshed.announcement).toBe("4 fresh picks loaded.");
    expect(refreshed.focusRequest).toBe(PRIMARY_ACTION_FOCUS_ID);
  });
});

describe("reduced motion", () => {
  it("drops the fling without touching the decision path", () => {
    const still = quickPicksReducer(start(), {
      type: "reduced-motion-changed",
      reducedMotion: true,
    });

    expect(cardMotion(start())).toBe("fling");
    expect(cardMotion(still)).toBe("none");
    expect(resolveSwipe({ dx: -SWIPE_DISTANCE_PX, dy: 0, elapsedMs: 200 })).toBe("dismiss");

    const decided = run(still, [
      { type: "action-requested", action: "dismiss", input: "gesture" },
      { type: "commit-succeeded", state: dismissed(cards[0].movieId) },
    ]);
    expect(currentCard(decided)?.movieId).toBe(cards[1].movieId);
    expect(decided.reducedMotion).toBe(true);
  });
});
