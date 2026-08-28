/**
 * The Quick Picks decision machine.
 *
 * One card, one decision, one canonical commit. Everything that decides what
 * the viewer sees next lives here as a pure reducer so the interesting rules
 * are testable without a browser: progress moves only on a committed watched
 * signal, a failed mutation leaves the card exactly where it was, an undone
 * dismissal comes back to the front of the queue, and the refetch policy is a
 * function of what happened rather than a timer.
 *
 * The three input routes — buttons, keyboard, pointer gestures — all arrive
 * here as the same `action-requested` event. That is the mechanism behind the
 * parity requirement: there is only one path to a decision, so a swipe cannot
 * drift from the button it is supposed to mirror.
 */

import type { MovieState, ServingPolicy } from "@/lib/api";
import {
  QUICK_PICK_SEMANTICS,
  quickPickProgress,
  type QuickPickActionKind,
  type QuickPickCard,
  type QuickPickCommitRequest,
  type QuickPickProgress,
} from "@/lib/quick-picks/contract";

/**
 * Refetch after this many committed watched signals. Low enough that the queue
 * reflects the persona's new exclusions quickly, high enough that the viewer is
 * not re-rendered under their own hand after every single decision.
 */
export const REFRESH_AFTER_POSITIVE_SIGNALS = 3;

export type QuickPickStatus = "deciding" | "committing" | "refreshing" | "exhausted";

export type QuickPickRefreshReason =
  | "exhausted"
  | "positive-signal-batch"
  | "threshold-reached"
  | "manual";

export type QuickPickInput = "button" | "keyboard" | "gesture";

export type QuickPickQueueSource = {
  cards: readonly QuickPickCard[];
  policy: ServingPolicy;
  /** Correlates the queue with the prediction audit that explains it. */
  requestId: string;
};

type PendingCommit = QuickPickCommitRequest & { input: QuickPickInput };

type UndoTarget = {
  card: QuickPickCard;
  /** The canonical revision returned by the dismissal that created it. */
  revision: number;
};

export type QuickPickState = {
  status: QuickPickStatus;
  queue: readonly QuickPickCard[];
  /** Decided in this session; cleared when a fresh queue arrives. */
  actedMovieIds: readonly number[];
  policy: ServingPolicy | null;
  queueRequestId: string | null;
  /** Watched signals committed since the current policy count was read. */
  committedPositiveSignals: number;
  positiveSignalsSinceLoad: number;
  pending: PendingCommit | null;
  undo: UndoTarget | null;
  /** Single polite announcement; replaced, never appended to. */
  announcement: string;
  error: string | null;
  refreshRequest: { reason: QuickPickRefreshReason; token: number } | null;
  /** DOM id the host should focus once the commit settles. */
  focusRequest: string | null;
  reducedMotion: boolean;
  decisions: number;
  /** Stops an empty refreshed queue from asking to refresh forever. */
  autoRefreshedOnExhaustion: boolean;
};

export type QuickPickEvent =
  | { type: "queue-loaded"; source: QuickPickQueueSource }
  | { type: "queue-failed"; message: string }
  | {
      type: "action-requested";
      action: QuickPickActionKind;
      input: QuickPickInput;
      /** Only read for `watched`; a star press is one action, not two. */
      rating?: number | null;
    }
  | { type: "commit-succeeded"; state: MovieState }
  | { type: "commit-failed"; message: string }
  | { type: "refresh-requested"; reason: QuickPickRefreshReason }
  | { type: "focus-restored" }
  | { type: "reduced-motion-changed"; reducedMotion: boolean };

export function actionFocusId(action: QuickPickActionKind): string {
  return `quick-pick-action-${action}`;
}

export const PRIMARY_ACTION_FOCUS_ID = actionFocusId("watched");

export function initialQuickPickState(
  source: QuickPickQueueSource | null,
  options: { reducedMotion?: boolean } = {},
): QuickPickState {
  const base: QuickPickState = {
    status: "exhausted",
    queue: [],
    actedMovieIds: [],
    policy: null,
    queueRequestId: null,
    committedPositiveSignals: 0,
    positiveSignalsSinceLoad: 0,
    pending: null,
    undo: null,
    announcement: "",
    error: null,
    refreshRequest: null,
    focusRequest: null,
    reducedMotion: options.reducedMotion ?? false,
    decisions: 0,
    autoRefreshedOnExhaustion: false,
  };
  return source ? loadQueue(base, source, { announce: false }) : base;
}

export function currentCard(state: QuickPickState): QuickPickCard | null {
  return state.queue[0] ?? null;
}

export function canUndo(state: QuickPickState): boolean {
  return state.undo !== null && state.pending === null && state.status !== "refreshing";
}

export function isBusy(state: QuickPickState): boolean {
  return state.pending !== null || state.status === "refreshing";
}

/** Reduced motion removes the fling, not the gesture. */
export function cardMotion(state: QuickPickState): "fling" | "none" {
  return state.reducedMotion ? "none" : "fling";
}

export function progressOf(state: QuickPickState) {
  return quickPickProgress(state.policy, state.committedPositiveSignals);
}

function loadQueue(
  state: QuickPickState,
  source: QuickPickQueueSource,
  options: { announce: boolean },
): QuickPickState {
  const empty = source.cards.length === 0;
  return {
    ...state,
    status: empty ? "exhausted" : "deciding",
    queue: source.cards,
    actedMovieIds: [],
    policy: source.policy,
    queueRequestId: source.requestId,
    // The returned policy count already includes everything committed so far,
    // so local additions start over rather than being counted twice.
    committedPositiveSignals: 0,
    positiveSignalsSinceLoad: 0,
    pending: null,
    // A dismissal stays undoable across a refresh: the canonical revision it
    // captured is still the one the undo has to assert against.
    undo: state.undo,
    error: null,
    refreshRequest: null,
    announcement: options.announce
      ? empty
        ? "No more picks are available for this persona right now."
        : `${source.cards.length} fresh picks loaded.`
      : state.announcement,
    focusRequest: options.announce && !empty ? PRIMARY_ACTION_FOCUS_ID : state.focusRequest,
    autoRefreshedOnExhaustion: empty,
  };
}

function requestRefresh(
  state: QuickPickState,
  reason: QuickPickRefreshReason,
): QuickPickState {
  return {
    ...state,
    status: "refreshing",
    refreshRequest: { reason, token: (state.refreshRequest?.token ?? 0) + 1 },
  };
}

/**
 * What the live region says about progress after a committed watched signal.
 *
 * A persona whose count is already past the threshold has to be described, not
 * counted: the visual meter clamps its fill, and the announcement used to read
 * the raw ratio out loud — "29 of 5 watched signals recorded" — to the one
 * audience that cannot see the clamped bar. Past the threshold the honest
 * statement is what it means, and it stays careful about the difference between
 * *enough signals recorded* and *serving has switched*, which only a returned
 * policy can say.
 */
export function progressAnnouncement(progress: QuickPickProgress): string {
  if (!progress.thresholdReached) {
    return (
      `${progress.count} of ${progress.threshold} watched signals recorded; ` +
      `${progress.remaining} to go.`
    );
  }
  return progress.learned
    ? "This persona is already being served by the learned path."
    : "Enough watched signals are recorded; the next ranked set can attempt learned serving.";
}

function commitAnnouncement(
  action: QuickPickActionKind,
  title: string,
  progressCopy: string | null,
): string {
  if (action === "undo-dismiss") {
    return `Dismissal undone. ${title} is back in the queue.`;
  }
  const head = `${title}: ${QUICK_PICK_SEMANTICS[action].label.toLowerCase()} saved.`;
  return progressCopy ? `${head} ${progressCopy}` : head;
}

function applySuccess(
  state: QuickPickState,
  pending: PendingCommit,
  committed: MovieState,
): QuickPickState {
  if (pending.action === "undo-dismiss") {
    const restored = state.undo;
    if (!restored) return { ...state, pending: null, status: "deciding" };
    return {
      ...state,
      status: "deciding",
      pending: null,
      undo: null,
      queue: [restored.card, ...state.queue],
      actedMovieIds: state.actedMovieIds.filter((id) => id !== restored.card.movieId),
      announcement: commitAnnouncement("undo-dismiss", restored.card.title, null),
      error: null,
      focusRequest: PRIMARY_ACTION_FOCUS_ID,
    };
  }

  const card = currentCard(state);
  if (!card || card.movieId !== pending.movieId) {
    return { ...state, pending: null, status: "deciding" };
  }

  // Progress is a claim about the durable record, so it moves only when the
  // canonical state that came back actually carries a watched timestamp.
  const earnedPositive =
    QUICK_PICK_SEMANTICS[pending.action].advancesPositiveProgress &&
    committed.watched_at !== null;
  const committedPositiveSignals =
    state.committedPositiveSignals + (earnedPositive ? 1 : 0);
  const positiveSignalsSinceLoad =
    state.positiveSignalsSinceLoad + (earnedPositive ? 1 : 0);

  const before = quickPickProgress(state.policy, state.committedPositiveSignals);
  const after = quickPickProgress(state.policy, committedPositiveSignals);
  const progressCopy = earnedPositive ? progressAnnouncement(after) : null;

  const queue = state.queue.slice(1);
  const next: QuickPickState = {
    ...state,
    status: "deciding",
    queue,
    actedMovieIds: [...state.actedMovieIds, card.movieId],
    committedPositiveSignals,
    positiveSignalsSinceLoad,
    pending: null,
    undo: pending.action === "dismiss" ? { card, revision: committed.revision } : null,
    announcement: commitAnnouncement(pending.action, card.title, progressCopy),
    error: null,
    focusRequest: actionFocusId(pending.action),
    decisions: state.decisions + 1,
  };

  if (queue.length === 0 && !next.autoRefreshedOnExhaustion) {
    return requestRefresh(next, "exhausted");
  }
  if (!before.thresholdReached && after.thresholdReached) {
    // Ask the API rather than announcing a transition we cannot see yet.
    return requestRefresh(next, "threshold-reached");
  }
  if (positiveSignalsSinceLoad >= REFRESH_AFTER_POSITIVE_SIGNALS) {
    return requestRefresh(next, "positive-signal-batch");
  }
  return queue.length === 0 ? { ...next, status: "exhausted" } : next;
}

export function quickPicksReducer(
  state: QuickPickState,
  event: QuickPickEvent,
): QuickPickState {
  switch (event.type) {
    case "queue-loaded":
      return loadQueue(state, event.source, {
        announce: state.queueRequestId !== null,
      });

    case "queue-failed":
      return {
        ...state,
        status: state.queue.length > 0 ? "deciding" : "exhausted",
        refreshRequest: null,
        error: event.message,
        announcement: `Fresh picks could not be loaded. ${event.message}`,
      };

    case "action-requested": {
      if (isBusy(state)) return state;
      if (event.action === "undo-dismiss") {
        if (!state.undo) return state;
        return {
          ...state,
          status: "committing",
          error: null,
          pending: {
            action: "undo-dismiss",
            movieId: state.undo.card.movieId,
            rating: null,
            expectedRevision: state.undo.revision,
            input: event.input,
          },
        };
      }
      const card = currentCard(state);
      if (!card) return state;
      return {
        ...state,
        status: "committing",
        error: null,
        pending: {
          action: event.action,
          movieId: card.movieId,
          rating: event.action === "watched" ? (event.rating ?? null) : null,
          // A queue card has no observed revision to assert against.
          expectedRevision: null,
          input: event.input,
        },
      };
    }

    case "commit-succeeded":
      return state.pending ? applySuccess(state, state.pending, event.state) : state;

    case "commit-failed": {
      const pending = state.pending;
      if (!pending) return state;
      const subject =
        pending.action === "undo-dismiss"
          ? (state.undo?.card.title ?? "That title")
          : (currentCard(state)?.title ?? "That title");
      return {
        ...state,
        status: "deciding",
        pending: null,
        error: event.message,
        announcement: `${subject} was not saved. ${event.message} The card is unchanged.`,
        focusRequest: actionFocusId(pending.action),
      };
    }

    case "refresh-requested":
      return state.pending
        ? state
        : requestRefresh({ ...state, error: null }, event.reason);

    case "focus-restored":
      return { ...state, focusRequest: null };

    case "reduced-motion-changed":
      return { ...state, reducedMotion: event.reducedMotion };
  }
}

export type SwipeVector = { dx: number; dy: number; elapsedMs: number };

/** Distance a pointer must travel before it counts as a deliberate swipe. */
export const SWIPE_DISTANCE_PX = 72;
export const SWIPE_MAX_DURATION_MS = 900;

/**
 * Gesture classification, kept pure so the swipe/button parity claim is a unit
 * test rather than a hopeful comment. Down is deliberately unmapped: it is the
 * direction people produce by accident while scrolling.
 */
export function resolveSwipe(vector: SwipeVector): QuickPickActionKind | null {
  if (vector.elapsedMs > SWIPE_MAX_DURATION_MS) return null;
  if (Math.abs(vector.dx) >= Math.abs(vector.dy)) {
    if (vector.dx <= -SWIPE_DISTANCE_PX) return "dismiss";
    if (vector.dx >= SWIPE_DISTANCE_PX) return "watchlist";
    return null;
  }
  return vector.dy <= -SWIPE_DISTANCE_PX ? "watched" : null;
}

const KEYBOARD_ACTIONS: Record<string, QuickPickActionKind> = {
  j: "dismiss",
  k: "watchlist",
  l: "watched",
  u: "undo-dismiss",
};

/**
 * `null` for anything modified, typed into a field, or simply unbound.
 *
 * Shift counts as a modifier, and it has to be caught even when the listener
 * does not forward `shiftKey`: `Shift+J` is a shortcut for something else in
 * plenty of tools, and lower-casing the key turned it into a dismissal. The
 * bindings are therefore matched case-sensitively — a shifted letter arrives as
 * an uppercase `key` — and an uppercase letter is only accepted when a caller
 * has explicitly said Shift is *not* down, which is the Caps Lock case.
 */
export function resolveKeyboardAction(event: {
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  inField?: boolean;
}): QuickPickActionKind | null {
  if (event.inField || event.altKey || event.ctrlKey || event.metaKey) return null;
  if (event.shiftKey) return null;
  const bound = KEYBOARD_ACTIONS[event.key];
  if (bound) return bound;
  return event.shiftKey === false
    ? (KEYBOARD_ACTIONS[event.key.toLowerCase()] ?? null)
    : null;
}

export const KEYBOARD_HINTS: Record<QuickPickActionKind, string> = {
  dismiss: "J",
  watchlist: "K",
  watched: "L",
  "undo-dismiss": "U",
};
