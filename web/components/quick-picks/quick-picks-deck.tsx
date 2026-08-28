"use client";

/**
 * Quick Picks: one movie, one decision, three equal input routes.
 *
 * The rules live in `lib/quick-picks/machine.ts`; this file is the surface that
 * feeds it. Buttons, the J/K/L/U keys, and a pointer swipe all dispatch the
 * same `action-requested` event, which is what makes "identical canonical
 * outcomes" a structural property rather than a promise.
 *
 * Two deliberate choices are worth knowing before reading:
 *
 * - The card advances only after the API confirms the write. A one-card queue
 *   that optimistically moves on and then rolls back would put a movie the
 *   viewer already dismissed back in front of them; waiting costs a short busy
 *   state and buys an interface that never lies about what was saved.
 * - Stars are a way of marking watched, not a second step after it. Pressing a
 *   star records watched *with* that rating in one mutation, so the rating can
 *   never disagree with the watched signal it implies.
 *
 * The deck does not name the persona. It printed `Exploring as {name}` until
 * the route moved inside `AppShell`, which prints the same sentence about 40px
 * above it; the design contract asks for one labelled persona, not for the same
 * claim twice. The shell owns it, here and everywhere else.
 */

import Image from "next/image";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { MovieRatingControl } from "@/components/movie/movie-state-controls";
import { PosterFallbackMark } from "@/components/movie/poster-card";
import { Icon } from "@/components/ui/icons";
import { REASON_DETAIL, ResourceProblem } from "@/components/ui/resource-region";
import type { RecommendationResponse } from "@/lib/api";
import {
  policyHeadline,
  QUICK_PICK_SEMANTICS,
  toQuickPickQueue,
  type QuickPickActionKind,
  type QuickPickCard,
} from "@/lib/quick-picks/contract";
import { evidenceSentence } from "@/lib/quick-picks/evidence";
import {
  actionFocusId,
  canUndo,
  cardMotion,
  currentCard,
  initialQuickPickState,
  isBusy,
  KEYBOARD_HINTS,
  progressOf,
  quickPicksReducer,
  resolveKeyboardAction,
  resolveSwipe,
  type QuickPickQueueSource,
} from "@/lib/quick-picks/machine";
import type {
  QuickPickQueuePayload,
  QuickPickTransport,
} from "@/lib/quick-picks/transport";
import {
  hasResourceData,
  isResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";
// The deck renders the product's shared poster fallback, so it needs the styles
// that mark is defined with; it is the only thing it borrows from that file.
import "@/components/movie/poster-card.css";
import "./quick-picks.css";

const DECISION_ORDER = ["dismiss", "watchlist", "watched"] as const;

const CARD_REGION_ID = "quick-pick-card";
const TITLE_ID = "quick-pick-title";
const EXHAUSTED_TITLE_ID = "quick-pick-exhausted-title";

// A store that never changes: the only signal wanted is server vs. client.
const subscribeNever = () => () => {};
const whileHydrated = () => true;
const beforeHydration = () => false;

function queueSource(
  state: ResourceState<RecommendationResponse>,
): QuickPickQueueSource | null {
  if (!hasResourceData(state)) return null;
  return {
    cards: toQuickPickQueue(state.data),
    policy: state.data.serving_policy,
    requestId: state.requestId,
  };
}

function failureMessage(state: ResourceState<RecommendationResponse>): string {
  return isResourceFailure(state)
    ? REASON_DETAIL[state.reason]
    : "The queue did not resolve.";
}

/** Only paths next/image is actually configured to serve are handed to it. */
function isRenderablePoster(url: string | null): url is string {
  return (
    url !== null &&
    (url.startsWith("/") || url.startsWith("https://image.tmdb.org/t/p/w500/"))
  );
}

export function QuickPicksDeck({
  browseHref,
  initial,
  transport,
}: {
  browseHref: string;
  initial: QuickPickQueuePayload;
  transport: QuickPickTransport;
}) {
  const [payload, setPayload] = useState(initial);
  const [state, dispatch] = useReducer(
    quickPicksReducer,
    queueSource(initial.queue),
    initialQuickPickState,
  );
  // Scoped to a movie id so the panel cannot carry one card's explanation onto
  // the next one.
  const [evidenceOpenFor, setEvidenceOpenFor] = useState<number | null>(null);
  const [seedTitles, setSeedTitles] = useState<Record<number, string | null>>({});
  // Buttons, keys, and swipes all do nothing until React has attached, so the
  // deck says when that has happened and the browser tests wait for it instead
  // of racing the first server-rendered paint. The server snapshot is what makes
  // this a hydration signal rather than a mount effect.
  const interactive = useSyncExternalStore(subscribeNever, whileHydrated, beforeHydration);

  const handledCommit = useRef<unknown>(null);
  const handledRefresh = useRef(0);

  const card = currentCard(state);
  const progress = progressOf(state);
  const busy = isBusy(state);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () =>
      dispatch({ type: "reduced-motion-changed", reducedMotion: query.matches });
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  // Commits are driven off the pending object's identity: the reducer creates a
  // fresh one per decision, so a re-render cannot fire the same write twice.
  useEffect(() => {
    const pending = state.pending;
    if (!pending || handledCommit.current === pending) return;
    handledCommit.current = pending;
    void transport.commit(pending).then((outcome) => {
      dispatch(
        outcome.ok
          ? { type: "commit-succeeded", state: outcome.state }
          : { type: "commit-failed", message: outcome.message },
      );
    });
  }, [state.pending, transport]);

  useEffect(() => {
    const request = state.refreshRequest;
    if (!request || handledRefresh.current === request.token) return;
    handledRefresh.current = request.token;
    void transport.refresh().then((next) => {
      const source = queueSource(next.queue);
      if (source) {
        setPayload(next);
        setEvidenceOpenFor(null);
        dispatch({ type: "queue-loaded", source });
        return;
      }
      // A failed refresh must not erase a queue the viewer can still use.
      setPayload((current) => (hasResourceData(current.queue) ? current : next));
      dispatch({ type: "queue-failed", message: failureMessage(next.queue) });
    });
  }, [state.refreshRequest, transport]);

  useEffect(() => {
    if (!state.focusRequest) return;
    const requested = document.getElementById(state.focusRequest);
    // The button that was just used is the best landing spot for a run of
    // decisions; when the deck moved on past it, fall back to whatever heading
    // now describes the page rather than dropping focus onto the body.
    const usable =
      requested instanceof HTMLButtonElement && !requested.disabled
        ? requested
        : (document.getElementById(TITLE_ID) ??
          document.getElementById(EXHAUSTED_TITLE_ID) ??
          document.getElementById(CARD_REGION_ID));
    usable?.focus();
    dispatch({ type: "focus-restored" });
  }, [state.focusRequest]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const inField = Boolean(
        target &&
          (target.isContentEditable ||
            ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)),
      );
      const action = resolveKeyboardAction({
        key: event.key,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        // `event.key` already carries the shift state as an uppercase letter,
        // so the resolver needs the flag to tell Shift+J (a modifier chord,
        // never a decision) from Caps Lock (`J` with Shift genuinely up, which
        // is still the viewer pressing the dismiss key).
        shiftKey: event.shiftKey,
        inField,
      });
      if (!action) return;
      event.preventDefault();
      dispatch({ type: "action-requested", action, input: "keyboard" });
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const openEvidence = useCallback(async () => {
    if (!card) return;
    setEvidenceOpenFor(card.movieId);
    const seedMovieId = payload.evidence[card.movieId]?.seedMovieId;
    if (!seedMovieId || seedMovieId in seedTitles) return;
    const title = await transport.resolveSeedTitle(seedMovieId);
    setSeedTitles((current) => ({ ...current, [seedMovieId]: title }));
  }, [card, payload.evidence, seedTitles, transport]);

  if (!hasResourceData(payload.queue)) {
    return (
      <div className="quick-picks-page" data-interactive={String(interactive)}>
        {isResourceFailure(payload.queue) ? (
          <ResourceProblem
            failure={payload.queue}
            label="Quick picks"
            onRetry={() => dispatch({ type: "refresh-requested", reason: "manual" })}
            reauthenticateHref="/"
          />
        ) : null}
      </div>
    );
  }

  const evidence = card ? payload.evidence[card.movieId] : undefined;
  const seedTitle = evidence?.seedMovieId ? (seedTitles[evidence.seedMovieId] ?? null) : null;
  // The machine's copy and the loaded response are the same object; naming one
  // source keeps the panel from ever describing two different policies.
  const policy = state.policy ?? payload.queue.data.serving_policy;

  return (
    <div className="quick-picks-page" data-interactive={String(interactive)}>
      <header className="quick-picks-header">
        <div>
          <p className="eyebrow">Quick picks</p>
        </div>
        <div className="quick-picks-header-actions">
          {payload.queue.source === "recorded-contract-fixture" ? (
            <span className="quick-picks-badge">Recorded fixture</span>
          ) : null}
          <Link className="button-secondary" href={browseHref}>
            Exit to Browse
          </Link>
        </div>
      </header>

      {card ? (
        <DecisionCard
          browseHref={browseHref}
          busy={busy}
          card={card}
          decisions={state.decisions}
          evidenceOpen={evidenceOpenFor === card.movieId}
          evidenceSentenceText={evidenceSentence(evidence, seedTitle)}
          motion={cardMotion(state)}
          onAction={(action, input, rating) =>
            dispatch({ type: "action-requested", action, input, rating })
          }
          onCloseEvidence={() => setEvidenceOpenFor(null)}
          onOpenEvidence={() => void openEvidence()}
          pendingAction={state.pending?.action ?? null}
          remaining={state.queue.length}
        />
      ) : (
        <ExhaustedState
          browseHref={browseHref}
          busy={busy}
          decisions={state.decisions}
          onRestart={() => dispatch({ type: "refresh-requested", reason: "manual" })}
        />
      )}

      <div className="quick-picks-aside">
        <ProgressPanel
          excludedCount={policy.excluded_count}
          filterPolicy={policy.filter_policy}
          headline={policyHeadline(policy)}
          learned={progress.learned}
          policyCount={progress.count}
          remaining={progress.remaining}
          requestId={payload.queue.requestId}
          threshold={progress.threshold}
          thresholdReached={progress.thresholdReached}
        />

        {/*
          Kept mounted while its own commit is in flight so the control the
          viewer just pressed does not vanish out from under their focus.
        */}
        {state.undo ? (
          <button
            className="button-secondary quick-picks-undo"
            disabled={!canUndo(state)}
            id={actionFocusId("undo-dismiss")}
            onClick={() =>
              dispatch({ type: "action-requested", action: "undo-dismiss", input: "button" })
            }
            type="button"
          >
            <Icon name="arrow" />
            Undo not for me for {state.undo.card.title}
            {state.pending?.action === "undo-dismiss" ? (
              <span className="quick-pick-saving">Saving…</span>
            ) : (
              <kbd>{KEYBOARD_HINTS["undo-dismiss"]}</kbd>
            )}
          </button>
        ) : null}

        {state.error ? <p className="quick-picks-error">{state.error}</p> : null}
      </div>

      {/*
        One polite channel for every mutation and progress change. The visible
        copy repeats it for sighted viewers and is hidden from assistive tech so
        the same sentence is not announced twice.
      */}
      <p aria-live="polite" className="visually-hidden" role="status">
        {state.announcement}
      </p>
      {state.announcement ? (
        <p aria-hidden="true" className="quick-picks-status">
          {state.announcement}
        </p>
      ) : null}
    </div>
  );
}

function DecisionCard({
  browseHref,
  busy,
  card,
  decisions,
  evidenceOpen,
  evidenceSentenceText,
  motion,
  onAction,
  onCloseEvidence,
  onOpenEvidence,
  pendingAction,
  remaining,
}: {
  browseHref: string;
  busy: boolean;
  card: QuickPickCard;
  decisions: number;
  evidenceOpen: boolean;
  evidenceSentenceText: string | null;
  motion: "fling" | "none";
  onAction: (
    action: QuickPickActionKind,
    input: "button" | "gesture",
    rating?: number | null,
  ) => void;
  onCloseEvidence: () => void;
  onOpenEvidence: () => void;
  pendingAction: QuickPickActionKind | null;
  remaining: number;
}) {
  const [drag, setDrag] = useState<{ dx: number; dy: number } | null>(null);
  const [posterFailed, setPosterFailed] = useState<string | null>(null);
  const origin = useRef<{ x: number; y: number; at: number } | null>(null);
  const posterUrl = card.posterUrl;
  const showPoster = isRenderablePoster(posterUrl) && posterFailed !== posterUrl;

  function onPointerDown(event: React.PointerEvent<HTMLElement>) {
    // A secondary touch in a multi-touch gesture is not a decision.
    if (busy || event.isPrimary === false) return;
    // The swipe surface is the whole card, so a drag that starts on a control
    // has to stay that control's business — otherwise dragging across the star
    // row would resolve as a decision, and capturing the pointer here would
    // retarget the click away from the button that was pressed.
    const onControl =
      event.target instanceof Element &&
      event.target.closest('button, a, input, [role="button"]') !== null;
    if (onControl) return;
    origin.current = { x: event.clientX, y: event.clientY, at: event.timeStamp };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event: React.PointerEvent<HTMLElement>) {
    const start = origin.current;
    if (!start || motion === "none") return;
    setDrag({ dx: event.clientX - start.x, dy: event.clientY - start.y });
  }

  function onPointerUp(event: React.PointerEvent<HTMLElement>) {
    const start = origin.current;
    origin.current = null;
    setDrag(null);
    if (!start) return;
    // A drag that left a text selection behind was a selection. Now that the
    // whole card is the swipe surface, most of what is under the pointer is
    // prose, and 72px inside 900ms is an entirely ordinary highlight — reading
    // that as "not for me" would be a decision the viewer never made.
    if (globalThis.getSelection?.()?.isCollapsed === false) return;
    const action = resolveSwipe({
      dx: event.clientX - start.x,
      dy: event.clientY - start.y,
      elapsedMs: event.timeStamp - start.at,
    });
    if (action) onAction(action, "gesture");
  }

  function onPointerCancel() {
    origin.current = null;
    setDrag(null);
  }

  return (
    <section
      aria-labelledby={TITLE_ID}
      className="quick-pick-card"
      // Lets the service-backed journey check the same id against serving.
      data-movie-id={card.movieId}
      id={CARD_REGION_ID}
      // The gesture belongs to the card, not to the 136×204 poster it used to
      // be bound to: on a phone the poster is a fraction of what reads as the
      // thing being swiped, and a swipe that started on the title did nothing.
      onPointerCancel={onPointerCancel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      tabIndex={-1}
    >
      <div
        className="quick-pick-poster"
        data-motion={motion}
        // The transform stays on the poster — it is the card's moving part, and
        // flinging the copy and the buttons with it would be a different, and
        // much louder, animation.
        style={drag ? { transform: `translate(${drag.dx}px, ${drag.dy}px)` } : undefined}
      >
        {showPoster && posterUrl ? (
          <Image
            alt=""
            className="quick-pick-poster-image"
            // Native image dragging would cancel the pointer stream mid-swipe.
            draggable={false}
            fill
            // A poster that 404s left this one surface showing an empty frame
            // while every other surface named the gap.
            onError={() => setPosterFailed(posterUrl)}
            priority
            sizes="(max-width: 700px) 40vw, 340px"
            src={posterUrl}
          />
        ) : (
          <PosterFallbackMark title={card.title} />
        )}
      </div>

      <div className="quick-pick-copy">
        <p className="eyebrow">
          Pick {decisions + 1} · {remaining} left in this queue
        </p>
        <h1 className="display-title" id={TITLE_ID} tabIndex={-1}>
          {card.title}
        </h1>
        <p className="quick-pick-meta">
          {card.year ?? "Year unknown"}
          {card.genres.length > 0 ? ` · ${card.genres.join(" · ")}` : null}
        </p>
        <p className="quick-pick-overview">
          {card.overview ?? "No synopsis is available in the reviewed metadata snapshot yet."}
        </p>
        <p className="quick-pick-reason">{card.reason}</p>

        <div className="quick-pick-evidence">
          <button
            aria-expanded={evidenceOpen}
            className="button-quiet"
            onClick={evidenceOpen ? onCloseEvidence : onOpenEvidence}
            type="button"
          >
            <Icon name="info" />
            Why this?
          </button>
          {evidenceOpen ? (
            <p className="quick-pick-evidence-body">
              {evidenceSentenceText ??
                "No prediction audit was recorded for this queue, so there is nothing specific to show."}
            </p>
          ) : null}
        </div>

        <div className="quick-pick-actions">
          {DECISION_ORDER.map((action) => (
            <button
              className={action === "watched" ? "button-primary" : "button-secondary"}
              disabled={busy}
              id={actionFocusId(action)}
              key={action}
              onClick={() => onAction(action, "button")}
              type="button"
            >
              {action === "watchlist" ? <Icon name="bookmark" /> : null}
              {action === "watched" ? <Icon name="check" /> : null}
              {QUICK_PICK_SEMANTICS[action].label}
              {pendingAction === action ? (
                <span className="quick-pick-saving">Saving…</span>
              ) : (
                <kbd>{KEYBOARD_HINTS[action]}</kbd>
              )}
            </button>
          ))}
        </div>

        {/*
          The shared control, so a star is named and sized the same here as on
          detail, Discover, and Library. The note carries what is specific to
          this route: one press is one write that marks watched *and* rates.
        */}
        <MovieRatingControl
          busy={busy}
          idPrefix="quick-pick-rating"
          note={`A star saves this as watched and records the rating in one write. ${QUICK_PICK_SEMANTICS.watched.modelEffect}`}
          onRate={(value) => onAction("watched", "button", value)}
          rating={null}
          title={card.title}
        />

        <p className="quick-pick-gesture-hint">
          Swipe left for not for me, right for watchlist, up for watched. Every gesture has a
          button and a key. <Link href={browseHref}>Browse the full catalog</Link> instead.
        </p>
      </div>
    </section>
  );
}

function ExhaustedState({
  browseHref,
  busy,
  decisions,
  onRestart,
}: {
  browseHref: string;
  busy: boolean;
  decisions: number;
  onRestart: () => void;
}) {
  return (
    <section aria-labelledby={EXHAUSTED_TITLE_ID} className="quick-pick-exhausted">
      <h1 className="section-title" id={EXHAUSTED_TITLE_ID} tabIndex={-1}>
        That is every pick we have for now
      </h1>
      <p>
        {decisions === 0
          ? "There is nothing left to decide on for this persona right now."
          : `${decisions} ${decisions === 1 ? "decision is" : "decisions are"} recorded. Dismissed and watched titles are excluded from the next queue.`}
      </p>
      <div className="quick-pick-exhausted-actions">
        <button className="button-primary" disabled={busy} onClick={onRestart} type="button">
          {busy ? "Loading…" : "Get more picks"}
        </button>
        <Link className="button-secondary" href={browseHref}>
          Browse the catalog
        </Link>
      </div>
    </section>
  );
}

function ProgressPanel({
  excludedCount,
  filterPolicy,
  headline,
  learned,
  policyCount,
  remaining,
  requestId,
  threshold,
  thresholdReached,
}: {
  excludedCount: number;
  filterPolicy: string;
  headline: string;
  learned: boolean;
  policyCount: number;
  remaining: number;
  requestId: string;
  threshold: number;
  thresholdReached: boolean;
}) {
  const shown = Math.min(policyCount, threshold);
  return (
    <section aria-labelledby="quick-pick-progress-title" className="quick-pick-progress">
      <h2 className="quick-pick-progress-title" id="quick-pick-progress-title">
        {headline}
      </h2>
      <p className="quick-pick-progress-count">
        {shown} of {threshold} positive watched signals
      </p>
      <div
        aria-label={`${shown} of ${threshold} positive watched signals recorded`}
        aria-valuemax={threshold}
        aria-valuemin={0}
        aria-valuenow={shown}
        className="quick-pick-progress-bar"
        role="progressbar"
      >
        <span style={{ width: `${(shown / threshold) * 100}%` }} />
      </div>
      <p className="quick-pick-progress-note">
        {learned
          ? "The last response reported learned serving."
          : thresholdReached
            ? `${threshold} signals are recorded. The last response still used the popularity fallback.`
            : `${remaining} more watched ${remaining === 1 ? "signal" : "signals"} before learned serving can be used.`}
      </p>
      <p className="quick-pick-progress-meta">
        {excludedCount} titles excluded · {filterPolicy} · Request {requestId}
      </p>
    </section>
  );
}
