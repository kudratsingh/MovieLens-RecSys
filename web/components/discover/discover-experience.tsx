"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FEATURED_MOVIE_ID, FeaturedMovie } from "@/components/discover/featured-movie";
import { QuickPicksEntry } from "@/components/discover/quick-picks-entry";
import {
  featuredIndex,
  featuredItem,
  initialQueue,
  mergeBehindCursor,
  QUEUE_EXTENSION_TRIGGER,
  recordDecision,
  remainingAfterFeatured,
  restoreQueue,
  skipFeatured,
  upcomingItems,
  type DiscoverQueue,
  type FeaturedPassOver,
} from "@/components/discover/queue";
import { WhyThis } from "@/components/discover/why-this";
import type { PreloadedTechnicalEvidence } from "@/components/discover/technical-evidence";
import { MovieRail } from "@/components/movie/movie-rail";
import {
  MovieRatingControl,
  MovieStateControls,
  RECOMMENDATION_CONTROLS,
} from "@/components/movie/movie-state-controls";
import { ResourceProblem, ResourceRegion } from "@/components/ui/resource-region";
import { EmptyState, PosterSkeleton } from "@/components/ui/resource-states";
import type {
  MovieState,
  RecommendationItem,
  RecommendationResponse,
  UserPreferences,
} from "@/lib/api";
import {
  DEFAULT_FEATURED_PREFERENCE,
  featuredPassOver,
  featuredPreferenceFrom,
  isWatchlisted,
  NUDGE_CONFIRM,
  NUDGE_DISMISS,
  NUDGE_QUESTION,
  SETTING_EYEBROW,
  SETTING_LABEL,
  settingNote,
  SKIP_LABEL,
  skipAnnouncement,
  WATCHLIST_CUE,
  type FeaturedPreference,
} from "@/lib/discover/featured-preference";
import { recommendationCards } from "@/lib/discover/movie-card";
import { describeServingPolicy } from "@/lib/discover/policy";
import {
  bffPreferenceClient,
  type PreferenceClient,
} from "@/lib/discover/preference-client";
import {
  markNudgeAnswered,
  nudgeEarnedBy,
  recordWatchlistSkip,
} from "@/lib/discover/skip-counter";
import { displayTitle, type MovieCard } from "@/lib/movie-types";
import {
  applyActionToDisplay,
  decisionDirection,
  displayState,
  ratingAction,
  sameAction,
  UNKNOWN_MOVIE_STATE,
  type DecisionDirection,
  type MovieDisplayState,
  type MovieStateAction,
} from "@/lib/movie-state/actions";
import {
  movieStateAnnouncement,
  type MovieStateOutcome,
} from "@/lib/movie-state/announce";
import { bffMovieStateClient, type MovieStateClient } from "@/lib/movie-state/client";
import { readCommittedStates } from "@/lib/movie-state/committed-store";
import { restoreFocus, restoreFocusInPlace } from "@/lib/movie-state/focus";
import {
  newIdempotencyKey,
  type MovieStateMutationResult,
} from "@/lib/movie-state/mutate";
import { RECOMMENDATIONS } from "@/lib/resources/definitions";
import { readBffResource } from "@/lib/resources/browser";
import {
  hasResourceData,
  isResourceFailure,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";
import "./discover.css";

const STATUS_ANCHOR = "discover-status";

/**
 * The permanent `Featured picks` switch, named so the one-time nudge can hand
 * focus to it after it unmounts itself. A viewer who answers the question and
 * then changes their mind has to land somewhere that can change it back.
 */
const FEATURED_PICKS_TOGGLE_ID = "featured-picks-toggle";
const FEATURED_PICKS_NOTE_ID = "featured-picks-note";

/** How long the undo offer stands. Long enough to notice, short enough to pass. */
const UNDO_WINDOW_MS = 8_000;

/**
 * How long the rating confirmation stands before the page goes quiet again.
 *
 * Long enough to read one sentence, short enough that it cannot still be on
 * screen over a movie it is not about. It is a reading interval rather than a
 * transition, so none of the motion tokens (120 ms, 220 ms) is the right value
 * to borrow — those describe how fast a thing moves, not how long a sentence
 * stays worth reading.
 */
const RATING_CONFIRMATION_MS = 4_000;

/**
 * The first control this surface declares, which is where a decision made
 * below the fold hands focus back. Read off the control set rather than named
 * again here: the order the set declares *is* the documented hierarchy, and a
 * second copy of it would be free to disagree.
 */
const FIRST_FEATURED_CONTROL = RECOMMENDATION_CONTROLS[0].kind;

/**
 * Where a decision was made, which is what decides what happens after it.
 *
 * A press on the featured card or a rail card is one step through the ranked
 * set: the cursor advances, the route re-reads, and the status line reports on
 * both. A rating made in the follow-up panel is neither — the title left the
 * ranked set when it was marked watched, and the star reaches no model input
 * (ADR 0012) — so that press ends by handing the viewer back to the movie
 * rather than by narrating a refresh it cannot have caused.
 */
type DecisionOrigin = "ranked-set" | "just-watched";

type Flow =
  | { kind: "idle" }
  | { kind: "saving"; message: string }
  | { kind: "refreshing"; message: string }
  /** `moved` is what separates a real refresh from a re-render of the same set. */
  | { kind: "refreshed"; message: string; moved: boolean }
  | { kind: "refresh-failed"; message: string; failure: ResourceFailure }
  /** A rating from the follow-up panel: settled copy, and no refresh tail. */
  | { kind: "rated"; message: string }
  /**
   * A pass-over. It is its own kind rather than a `refreshed` with different
   * words because nothing was written and nothing was re-read: the sentence has
   * to be able to say that, and a `refreshed` frame would eventually grow a
   * "Recommendations refreshed" tail onto a decision that made no request.
   */
  | { kind: "skipped"; message: string }
  /** The `Featured picks` setting answering for itself. */
  | { kind: "preference"; message: string }
  | { kind: "error"; message: string };

/** The in-flight frame for the one movie currently being written. */
type Optimistic = { movieId: number; state: MovieDisplayState };

/** One viewer decision, and the key every attempt at it has to carry. */
type Intent = { movieId: number; action: MovieStateAction; key: string };

/**
 * A decision that is exactly reversible, held for as long as the offer stands.
 *
 * Watched is deliberately absent. `frontend-system.md`'s control-set table
 * declares `watched: final` on Discover because reversing watched history is a
 * confirmed destructive edit that belongs on detail and in the Library, and a
 * bare `Undo` here would quietly become a second way to do it. The honest
 * affordance after a watched decision is the rating prompt and a Library link.
 */
type UndoOffer = {
  movieId: number;
  title: string;
  /** The write that reverses the committed one. */
  action: MovieStateAction;
  /** The revision the commit returned, so the reversal asserts a real one. */
  revision: number;
  label: string;
};

function rankedIds(state: ResourceState<RecommendationResponse>): readonly number[] {
  return hasResourceData(state) ? state.data.items.map((item) => item.movie_id) : [];
}

function rankedItems(
  state: ResourceState<RecommendationResponse>,
): readonly RecommendationItem[] {
  return hasResourceData(state) ? state.data.items : [];
}

/** The canonical records a response carried, keyed for `adopt`. */
function statesById(
  items: readonly RecommendationItem[],
): Record<number, MovieState> {
  const states: Record<number, MovieState> = {};
  for (const item of items) {
    if (item.state) states[item.movie_id] = item.state;
  }
  return states;
}

/**
 * The tab's session storage, or nothing.
 *
 * Read through a function rather than held in a variable so the server render
 * and the browser render agree: this component is rendered on both sides, and a
 * module-scope read would be baked into the HTML.
 */
function sessionStore(): Storage | null {
  return typeof window === "undefined" ? null : window.sessionStorage;
}

/** Same ids in the same order: the viewer would see no difference. */
function sameRankedSet(before: readonly number[], after: readonly number[]): boolean {
  return (
    before.length === after.length && before.every((id, index) => id === after[index])
  );
}

/**
 * Whether this action is what "just marked watched" means. Reading it off the
 * *result* instead — any committed state that happens to carry `watched_at` —
 * pops the rating panel after a watchlist press or a dismissal on a title that
 * was already watched.
 */
function startsWatchedHistory(
  action: MovieStateAction,
  before: MovieDisplayState,
): boolean {
  if (action.method !== "PUT") return false;
  if (action.resource === "watched") return true;
  // A rating implies watched, so it opens the panel — but only when it is the
  // interaction that created the history, not when it is the panel's own edit.
  return action.resource === "rating" && !before.watched;
}

/**
 * How a decision that did not commit is described.
 *
 * The three outcomes are genuinely different events and the copy has to say so:
 * a race the write path could not settle, a rule the API declined to break, and
 * a request that did not arrive. Named once because both the decision path and
 * the undo path answer it identically.
 */
function decisionOutcome(
  result: Exclude<MovieStateMutationResult, { status: "committed" }>,
): MovieStateOutcome {
  if (result.status === "conflict") return { kind: "conflict" };
  if (result.status === "refused") return { kind: "refused", detail: result.detail };
  return { kind: "failed", failure: result.failure };
}

/** The offer a committed decision leaves behind, or nothing when it is final. */
function undoOfferFor(
  movie: MovieCard,
  action: MovieStateAction,
  state: MovieState,
): UndoOffer | null {
  if (action.resource === "watchlist") {
    return {
      movieId: movie.id,
      title: movie.title,
      action: { resource: "watchlist", method: action.method === "PUT" ? "DELETE" : "PUT" },
      revision: state.revision,
      label:
        action.method === "PUT"
          ? `Undo saving ${movie.title} to the watchlist`
          : `Undo removing ${movie.title} from the watchlist`,
    };
  }
  if (action.resource === "dismissal") {
    return {
      movieId: movie.id,
      title: movie.title,
      action: { resource: "dismissal", method: action.method === "PUT" ? "DELETE" : "PUT" },
      revision: state.revision,
      label:
        action.method === "PUT"
          ? `Undo dismissing ${movie.title}`
          : `Undo restoring ${movie.title}`,
    };
  }
  return null;
}

/**
 * Reduced motion is read rather than assumed so the advance has a branch a test
 * can drive. The global stylesheet also neutralises animation under the same
 * query; this is what lets the component say what it did, not only look right.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);
  return reduced;
}

/**
 * The interactive half of `/discover`.
 *
 * It is seeded with the server-loaded recommendation state rather than
 * fetching on mount, so the first movie is in the initial HTML. After that it
 * owns four things the server cannot: the queue position the featured slot
 * reads from, the optimistic frame of a mutation, the canonical revision each
 * committed response returns, and the re-read that makes "Recommendations
 * refreshed" true rather than decorative. That claim is only ever made after
 * the refetch resolves *and* the set it returned is different — a watchlist
 * press legitimately leaves the ranked order alone, and saying otherwise is how
 * a working button came to look broken.
 *
 * The featured slot is a cursor into a queue this route owns (`queue.ts`), not
 * a projection of the last response's first item. Every committed decision
 * moves it — watchlist included, because on Discover `Watchlist` means *save
 * it, next* — and it moves only after the API commits, because an advance that
 * rolled back would re-show a title the viewer had already dismissed.
 *
 * The recommendation contract carries no per-item state, so every card starts
 * from "nothing known" and two mechanisms fill that in: the states other routes
 * have already committed are folded in from the tab-local relay before the
 * cards render, and anything the relay does not know is corrected by the shared
 * write path, which re-reads the canonical record and replays the same intent
 * against it rather than discarding the press. Per-card state reads are
 * deliberately not the answer here: that is the fan-out the local catalog
 * snapshot exists to prevent.
 */
export function DiscoverExperience({
  userId,
  personaName,
  initialRecommendations,
  initialPreferences,
  recordedEvidence,
  browseHref,
  quickPicksHref,
  movieHrefBase,
  movieHrefQuery = "",
  limit,
  stateClient = bffMovieStateClient,
  preferenceClient = bffPreferenceClient,
}: {
  userId: number;
  personaName: string;
  initialRecommendations: ResourceState<RecommendationResponse>;
  /**
   * The `Featured picks` setting as the server read it. A failed read is the
   * documented default rather than an error region — the setting decides which
   * of two honest cards leads, so it must never be the reason no movie loads.
   */
  initialPreferences?: ResourceState<UserPreferences>;
  recordedEvidence?: PreloadedTechnicalEvidence | null;
  browseHref: string;
  quickPicksHref: string;
  /**
   * Built from strings rather than a callback: this component is a client
   * boundary, and a server component cannot hand a function across it.
   */
  movieHrefBase: string;
  movieHrefQuery?: string;
  limit: number;
  stateClient?: MovieStateClient;
  preferenceClient?: PreferenceClient;
}) {
  const movieHrefFor = (movieId: number) =>
    `${movieHrefBase}/${movieId}${movieHrefQuery}`;
  // Named once: the rating panel and the confirmation that replaces it are the
  // same offer at two moments, and they have to point at the same place.
  const libraryHref = `/library?userId=${userId}`;
  const router = useRouter();
  const [recommendations, setRecommendations] = useState(initialRecommendations);
  const [queue, setQueue] = useState<DiscoverQueue>(() =>
    initialQueue(rankedItems(initialRecommendations)),
  );
  /**
   * Canonical records this route has learned, by movie. Never a guess.
   *
   * Seeded from the first response's own per-item state, which is the reason
   * that field exists (ADR 0012): without it a reloaded page knows nothing
   * about a title it watchlisted a minute ago, so the featured slot could not
   * say `On your watchlist` and could not offer a Skip that means anything.
   * The tab-local relay still fills in what the response has not caught up to.
   */
  const [known, setKnown] = useState<Record<number, MovieState>>(() =>
    statesById(rankedItems(initialRecommendations)),
  );
  const [optimistic, setOptimistic] = useState<Optimistic | null>(null);
  const [pendingMovieId, setPendingMovieId] = useState<number | null>(null);
  const [flow, setFlow] = useState<Flow>({ kind: "idle" });
  // Held as a snapshot rather than a lookup: a watched title leaves the ranked
  // set on the next request, and the rating panel has to outlive it.
  const [justWatched, setJustWatched] = useState<MovieCard | null>(null);
  const [undo, setUndo] = useState<UndoOffer | null>(null);
  /** The direction the last advance travelled, for the incoming card. */
  const [advanceFrom, setAdvanceFrom] = useState<DecisionDirection | null>(null);
  const [extending, setExtending] = useState(false);
  /** What the viewer is shown, never what the recommender is told. */
  const [preference, setPreference] = useState<FeaturedPreference>(() =>
    initialPreferences
      ? featuredPreferenceFrom(initialPreferences)
      : DEFAULT_FEATURED_PREFERENCE,
  );
  const [preferenceBusy, setPreferenceBusy] = useState(false);
  /** The one-time offer, raised by the third skip and settled by either answer. */
  const [nudge, setNudge] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  // Serialising writes is what keeps `expected_revision` meaningful: two
  // presses in flight together would both assert the revision they started
  // from, and the second response would land on top of the first.
  const inFlight = useRef(false);
  // The decision currently being attempted, with the key it was minted under.
  // Re-pressing a control after a failure is one intent, not two, so it keeps
  // the key and the API replays instead of writing a second feedback event.
  const intent = useRef<Intent | null>(null);
  /** Queue length at the last extension attempt, so it asks once per answer. */
  const extendedAt = useRef(-1);
  /**
   * Mirrors the queue for the write path. A commit reads the queue, decides
   * where the cursor lands, and writes it back — and a background extension
   * that resolves in between would be discarded if that read came from a
   * render closure. Only `applyQueue` writes either one.
   */
  const queueRef = useRef(queue);
  /**
   * The pass-over rule as the last render computed it, for the paths that run
   * outside a render: a commit that has to know which card the viewer lands on,
   * and a merge that has to know which card must not be replaced. Kept in sync
   * by an effect rather than assigned during render, so it is never a value
   * from a render React discarded.
   */
  const passOverRef = useRef<FeaturedPassOver>(() => false);

  const applyQueue = useCallback(
    (update: (current: DiscoverQueue) => DiscoverQueue): DiscoverQueue => {
      const next = update(queueRef.current);
      queueRef.current = next;
      setQueue(next);
      return next;
    },
    [],
  );

  /**
   * Adopts a canonical record the API committed, wherever it came from: this
   * session's own write, a recommendation response's per-item state, the
   * conflict re-read, or the relay another route left behind. A record only
   * wins if it is newer than what is already held, so an older echo can never
   * overwrite a fresher answer.
   */
  const adopt = useCallback((states: readonly MovieState[]) => {
    setKnown((current) => {
      let changed = false;
      const next = { ...current };
      for (const state of states) {
        const held = current[state.movie_id];
        if (held && held.revision >= state.revision) continue;
        next[state.movie_id] = state;
        changed = true;
      }
      return changed ? next : current;
    });
  }, []);

  const refetch = useCallback(
    () =>
      readBffResource(
        RECOMMENDATIONS,
        `/api/users/${userId}/recommendations?limit=${limit}`,
      ),
    [limit, userId],
  );

  /**
   * Folds a response into the queue behind the card being read. Every path that
   * reads the ranked set goes through here, so there is one rule for what
   * happens to the card being read: nothing.
   *
   * The per-item states travel with it. They are canonical records from the API
   * and go through `adopt` like any other, so a response can correct what this
   * session believes about a title but an older echo can never overwrite a
   * fresher answer.
   */
  const absorb = useCallback(
    (incoming: readonly RecommendationItem[]) => {
      const next = applyQueue((current) =>
        mergeBehindCursor(current, incoming, passOverRef.current),
      );
      extendedAt.current = next.items.length;
      const states = Object.values(statesById(incoming));
      if (states.length) adopt(states);
    },
    [adopt, applyQueue],
  );

  const reload = useCallback(async () => {
    setRecommendations({ status: "loading", resource: "recommendations" });
    const next = await refetch();
    setRecommendations(next);
    if (hasResourceData(next)) absorb(next.data.items);
  }, [absorb, refetch]);

  /**
   * Hands the page back to the featured movie after a decision made below it.
   *
   * The rating panel sits under the ranked card and, at 390px, well below the
   * fold: committing there and leaving the viewport where it was left the
   * viewer reading a finished panel about a title the featured slot had already
   * moved past. Scroll and focus travel together, because a scroll a keyboard
   * reader cannot follow is not a return.
   *
   * Called from the commit path rather than from an effect, so it runs exactly
   * once per decision — a re-render that repeated it would drag back a viewer
   * who has since scrolled somewhere on purpose.
   */
  const returnToFeatured = useCallback(() => {
    if (typeof document === "undefined") return;
    // `nearest`, not `start`: on a wide screen the movie and the panel are both
    // on one screenful, and `start` would answer a rating by scrolling the
    // viewer *down* past the page header to satisfy an alignment nobody asked
    // for. `nearest` moves only when the movie is genuinely off screen, which is
    // the case this exists for. The call itself is optional because jsdom
    // implements neither this method nor a viewport; the focus move below is the
    // half that must not be.
    document.getElementById(FEATURED_MOVIE_ID)?.scrollIntoView?.({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "nearest",
    });
    const featured = featuredItem(queueRef.current, passOverRef.current);
    restoreFocusInPlace(
      featured ? `featured-${featured.movie_id}-${FIRST_FEATURED_CONTROL}` : null,
      STATUS_ANCHOR,
    );
  }, [reducedMotion]);

  // Reading the relay is an external read, not something derivable during
  // render: the server renders this component too and has no session storage,
  // so folding it in at render time would make the two disagree. It re-runs
  // when the queue changes, because a merged set may contain movies the
  // previous one did not.
  useEffect(() => {
    // Reading session storage and adopting what it holds is the effect's whole
    // job, and the rule's usual advice — derive it during render instead — is
    // the one thing that cannot work here: the server renders this component
    // too and has no session storage, so the two renders would disagree about
    // what the cards show. The state set here is the external read.
    if (typeof window === "undefined") return;
    const relayed = readCommittedStates(window.sessionStorage, userId);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (relayed.size) adopt([...relayed.values()]);
  }, [adopt, queue, userId]);

  // The offer is time-boxed rather than permanent: an `Undo` that outlives the
  // decision it belongs to becomes a button whose target the viewer can no
  // longer name.
  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(null), UNDO_WINDOW_MS);
    return () => window.clearTimeout(timer);
  }, [undo]);

  // The rating confirmation is a moment, not a state. It clears on its own here
  // and immediately on the next decision, because `commit` opens by replacing
  // the flow — a sentence about one movie left standing over another is the
  // same defect as the panel that used to outlive its title, one size smaller.
  useEffect(() => {
    if (flow.kind !== "rated") return;
    const timer = window.setTimeout(
      () => setFlow({ kind: "idle" }),
      RATING_CONFIRMATION_MS,
    );
    return () => window.clearTimeout(timer);
  }, [flow]);

  const cardStates = useMemo(() => {
    const states: Record<number, MovieDisplayState> = {};
    for (const [movieId, state] of Object.entries(known)) {
      states[Number(movieId)] = displayState(state);
    }
    if (optimistic) states[optimistic.movieId] = optimistic.state;
    return states;
  }, [known, optimistic]);

  // Which titles may not take the featured slot: the ones this session skipped
  // by hand, and — when the preference is off — the ones already on the
  // watchlist. Built here rather than inside the queue so the queue stays a
  // list with a cursor and knows nothing about watchlists or settings.
  const passOver = useMemo(
    () =>
      featuredPassOver({
        preference,
        states: cardStates,
        skipped: queue.skipped,
      }),
    [cardStates, preference, queue.skipped],
  );
  useEffect(() => {
    passOverRef.current = passOver;
  }, [passOver]);

  const remaining = remainingAfterFeatured(queue, passOver);

  // Top up before the viewer arrives at the end. The post-decision refetch
  // usually gets there first; this is what covers a refetch that failed or
  // came back short, and it appends without touching the card being read.
  // `remaining` counts what could still be *featured*, so a queue whose whole
  // tail is held back tops itself up rather than sitting on an empty slot.
  useEffect(() => {
    if (remaining > QUEUE_EXTENSION_TRIGGER) return;
    if (!hasResourceData(recommendations)) return;
    if (inFlight.current || extendedAt.current === queue.items.length) return;
    extendedAt.current = queue.items.length;
    setExtending(true);
    void refetch().then((next) => {
      setExtending(false);
      // A failed extension is silent: the viewer still has cards, and the
      // region above them is reporting on a read that succeeded.
      if (hasResourceData(next)) absorb(next.data.items);
    });
  }, [absorb, queue.items.length, recommendations, refetch, remaining]);

  async function commit(
    movie: MovieCard,
    action: MovieStateAction,
    control: HTMLElement,
    origin: DecisionOrigin = "ranked-set",
  ) {
    if (inFlight.current) return;
    inFlight.current = true;

    const previous = intent.current;
    const key =
      previous && previous.movieId === movie.id && sameAction(previous.action, action)
        ? previous.key
        : newIdempotencyKey();
    intent.current = { movieId: movie.id, action, key };

    const currentState = cardStates[movie.id] ?? UNKNOWN_MOVIE_STATE;
    const advances = origin === "ranked-set";
    // The rating prompt belongs to one title. A decision on any other movie has
    // moved past it, and leaving it standing is how it ended up outliving the
    // page.
    if (justWatched && justWatched.id !== movie.id) setJustWatched(null);
    setUndo(null);

    setPendingMovieId(movie.id);
    setFlow({ kind: "saving", message: `Saving ${movie.title}…` });
    setOptimistic({
      movieId: movie.id,
      state: applyActionToDisplay(currentState, action),
    });

    const result = await stateClient.mutate({
      userId,
      movieId: movie.id,
      resource: action.resource,
      method: action.method,
      rating:
        action.resource === "rating" && action.method === "PUT"
          ? action.rating
          : undefined,
      // `0` is the canonical "no state yet" assertion; anything else is a
      // revision the server issued, never one invented here.
      expectedRevision: known[movie.id]?.revision ?? 0,
      idempotencyKey: key,
    });

    if (result.status !== "committed") {
      // Dropping the pending frame *is* the rollback: the card falls back to
      // the last canonical record this route holds, and the queue never moved.
      setOptimistic(null);
      setPendingMovieId(null);
      inFlight.current = false;
      if (result.status === "conflict") {
        // The write path already re-read the canonical record and replayed the
        // same intent against it, so reaching here means the stored state is
        // genuinely not what this press assumed. Adopt what came back and let
        // the next press be a fresh intent against a revision the server
        // issued — the stored key belongs to a revision that is gone.
        if (result.canonical) adopt([result.canonical]);
        intent.current = null;
      }
      setFlow({
        kind: "error",
        message: movieStateAnnouncement(
          decisionOutcome(result),
          { title: movie.title, voice: "discover" },
        ),
      });
      restoreFocus(control);
      return;
    }

    intent.current = null;
    // The committed state replaces the optimistic guess outright; a
    // reconciliation that kept any local field would be a second source of
    // truth for the same movie.
    adopt([result.state]);
    setOptimistic(null);

    // Commit first, then move. Everything below reads the queue as it will be
    // once this decision is recorded, so the announcement and the focus target
    // name the card the viewer is about to be looking at.
    const arriving = advances
      ? featuredItem(
          applyQueue((current) => recordDecision(current, movie.id)),
          passOverRef.current,
        )
      : null;
    if (advances) setAdvanceFrom(reducedMotion ? null : decisionDirection(action));

    const message = movieStateAnnouncement(
      { kind: "committed", action },
      {
        title: movie.title,
        voice: "discover",
        // The display title, not the raw one: a MovieLens title carries its own
        // year, and "Next: In the Mood for Love (2000)" is the doubled year
        // read out loud.
        next: arriving ? displayTitle(arriving.title, arriving.release_year) : null,
      },
    );
    if (startsWatchedHistory(action, currentState)) {
      setJustWatched({ ...movie, state: displayState(result.state) });
    }
    if (advances) setUndo(undoOfferFor(movie, action, result.state));

    if (origin === "just-watched") {
      // The panel's life is one decision long, and this is that decision. It
      // cannot have moved the ranked set — the watch excluded the title, and
      // the star reaches no model input — so there is no refresh worth
      // narrating, and the honest end of the interaction is one sentence plus
      // the movie the viewer is now on. Leaving the panel standing with its
      // stars filled in was the whole complaint: a finished decision that still
      // looked like an open one, at the bottom of a page the viewer had been
      // scrolled away from.
      setJustWatched(null);
      setPendingMovieId(null);
      inFlight.current = false;
      setFlow({ kind: "rated", message });
      returnToFeatured();
      // The re-read still happens, silently: the queue keeps topping itself up
      // and the server-rendered history below has changed. Neither is something
      // to make the viewer read past on the way back to choosing a movie, and a
      // failed one costs nothing here — the cards on screen are still good.
      void refetch().then((next) => {
        if (isResourceFailure(next)) return;
        setRecommendations(next);
        if (hasResourceData(next)) absorb(next.data.items);
      });
      router.refresh();
      return;
    }

    // Focus goes to the same control on the card that just arrived, so a run of
    // decisions stays under one finger; the status line is the fallback, never
    // the target, because landing there drops a keyboard reader who was five
    // cards into the rail back at the top of the page.
    restoreFocus(
      arriving ? `featured-${arriving.movie_id}-${action.resource}` : control,
      control,
      STATUS_ANCHOR,
    );

    const before = rankedIds(recommendations);
    setFlow({ kind: "refreshing", message });
    const next = await refetch();
    setPendingMovieId(null);
    inFlight.current = false;
    if (isResourceFailure(next)) {
      setFlow({ kind: "refresh-failed", message, failure: next });
    } else {
      setRecommendations(next);
      if (hasResourceData(next)) absorb(next.data.items);
      // Watchlist is organizational (ADR 0012), so a watchlist press commits
      // and the ranked set legitimately comes back identical. Saying it was
      // refreshed anyway is what made a working button look broken.
      setFlow({
        kind: "refreshed",
        message,
        moved: !sameRankedSet(before, rankedIds(next)),
      });
    }
    // Watch history is a server-rendered region on this page; the mutation
    // changed it too, so ask the server component tree to re-run.
    router.refresh();
  }

  async function runUndo(offer: UndoOffer, control: HTMLElement) {
    if (inFlight.current) return;
    inFlight.current = true;
    setUndo(null);
    setPendingMovieId(offer.movieId);
    setFlow({ kind: "saving", message: `Undoing ${offer.title}…` });

    const result = await stateClient.mutate({
      userId,
      movieId: offer.movieId,
      resource: offer.action.resource,
      method: offer.action.method,
      expectedRevision: offer.revision,
      idempotencyKey: newIdempotencyKey(),
    });

    setPendingMovieId(null);
    inFlight.current = false;
    if (result.status !== "committed") {
      if (result.status === "conflict" && result.canonical) adopt([result.canonical]);
      setFlow({
        kind: "error",
        message: movieStateAnnouncement(decisionOutcome(result), {
          title: offer.title,
          voice: "discover",
        }),
      });
      restoreFocus(control, STATUS_ANCHOR);
      return;
    }

    // Server state and cursor come back together: undoing the write without
    // restoring the position would leave the viewer past a card they asked to
    // return to.
    adopt([result.state]);
    applyQueue((current) => restoreQueue(current, offer.movieId));
    setJustWatched(null);
    setAdvanceFrom(null);
    setFlow({
      kind: "refreshed",
      message: `${offer.title} is back, and the change was undone.`,
      moved: true,
    });
    restoreFocus(`featured-${offer.movieId}-${offer.action.resource}`, STATUS_ANCHOR);
    router.refresh();
  }

  /**
   * Passes the featured title over. This is the one control on the surface that
   * makes no request at all.
   *
   * Nothing is written: not a watched, not a dismissal, not a rating, and
   * emphatically not a training negative (ADR 0012). The title keeps its
   * watchlist entry, keeps its place in the ranked set, and moves to the rail —
   * so the sentence says `still on your watchlist` rather than reporting a
   * decision, and there is no undo to offer because nothing was undone.
   *
   * It is deliberately not routed through `commit`: sharing that path would
   * mean sharing its idempotency key, its optimistic frame, its revision
   * assertion, and its refetch, all for a press that changes no state anywhere.
   */
  function skip(movie: MovieCard, control: HTMLElement) {
    // A write in flight owns the queue until it settles; moving the cursor out
    // from under it would make its `Next:` sentence name the wrong movie.
    if (inFlight.current) return;

    const next = applyQueue((current) => skipFeatured(current, movie.id));
    // Built from the queue this press just produced rather than from the
    // memoised predicate, which still has the pre-skip list.
    const arriving = featuredItem(
      next,
      featuredPassOver({ preference, states: cardStates, skipped: next.skipped }),
    );
    setUndo(null);
    // The rating prompt belongs to one title, and the page has moved past it.
    if (justWatched && justWatched.id !== movie.id) setJustWatched(null);
    // No direction: an advance animates the way a decision travelled, and a
    // skip is not a decision. The card swaps in place.
    setAdvanceFrom(null);
    setFlow({
      kind: "skipped",
      message: skipAnnouncement(
        movie.title,
        arriving ? displayTitle(arriving.title, arriving.release_year) : null,
      ),
    });

    const record = recordWatchlistSkip(sessionStore(), userId);
    if (nudgeEarnedBy(record)) setNudge(true);

    // The same control on the arriving card first, so a run of skips stays
    // under one finger — but only if that card offers one, which it does only
    // when it is watchlisted too.
    restoreFocus(
      arriving ? `featured-${arriving.movie_id}-skip` : null,
      arriving ? `featured-${arriving.movie_id}-${FIRST_FEATURED_CONTROL}` : control,
      control,
      STATUS_ANCHOR,
    );
  }

  /**
   * Writes the `Featured picks` setting and reports what is now stored.
   *
   * Separate from `commit` because it is a different kind of thing: it changes
   * what the viewer is shown and nothing the recommender reads, so it has no
   * optimistic frame worth keeping (the answer arrives in a request or two of
   * latency), no undo offer, and no refetch — the same response, re-ordered by
   * this client, is the whole effect.
   */
  async function applyPreference(
    featureWatchlistedTitles: boolean,
    control: HTMLElement,
    fallbackFocus: string,
  ) {
    if (preferenceBusy) return;
    setPreferenceBusy(true);
    const result = await preferenceClient.set({
      userId,
      featureWatchlistedTitles,
      expectedRevision: preference.revision,
    });
    setPreferenceBusy(false);
    setNudge(false);
    markNudgeAnswered(sessionStore(), userId);

    if (result.status === "committed") {
      setPreference(result.preference);
      setFlow({
        kind: "preference",
        message: result.preference.featureWatchlistedTitles
          ? "Watchlisted titles can be featured again."
          : "Watchlisted titles will not be featured. They stay in the ranked list below.",
      });
    } else if (result.status === "conflict") {
      // Somebody — another tab, another device — set it first. Adopting what is
      // stored and saying so beats leaving a toggle showing a value nobody has.
      if (result.canonical) setPreference(result.canonical);
      setFlow({
        kind: "error",
        message:
          "The Featured picks setting was changed somewhere else. The switch now shows what is stored.",
      });
    } else {
      setFlow({
        kind: "error",
        message: "The Featured picks setting could not be saved, so nothing changed.",
      });
    }
    restoreFocus(control, fallbackFocus, STATUS_ANCHOR);
  }

  function onAction(movie: MovieCard) {
    return (action: MovieStateAction, control: HTMLElement) => {
      void commit(movie, action, control);
    };
  }

  return (
    <ResourceRegion
      empty={
        <EmptyState
          action={
            <>
              <Link className="button-primary" href={browseHref}>
                Browse the catalog
              </Link>
              <QuickPicksEntry
                href={quickPicksHref}
                note="Or classify a handful quickly, so the next ranked set has something to work with."
              />
            </>
          }
          message="The recommendation API answered with no unseen titles for this persona. Browsing and marking a few movies gives it something to work with."
          title="No recommendations right now"
        />
      }
      label="Recommendations"
      loading={<PosterSkeleton count={5} />}
      onRetry={() => void reload()}
      state={recommendations}
    >
      {(data) => {
        // Ranks are positions in the queue rather than in the slice on screen,
        // so the seventh decision is not labelled "Rank 1" again.
        const cards = recommendationCards(queue.items, cardStates);
        const index = featuredIndex(queue, passOver);
        const item = index < 0 ? null : queue.items[index];
        // The rail's membership is the queue's rule, read back rather than
        // re-derived: a held-back title is still a recommendation and keeps its
        // card here, which is what makes `Skip` and the preference reversible
        // by looking rather than by remembering.
        const railIds = new Set(
          upcomingItems(queue, passOver).map((entry) => entry.movie_id),
        );
        const rest = cards.filter((card) => railIds.has(card.id));
        const featured = index < 0 ? null : cards[index];
        const policy = describeServingPolicy(data);

        const status = (
          <FlowStatus
            featureWatchlistedTitles={preference.featureWatchlistedTitles}
            flow={flow}
            libraryHref={libraryHref}
            nudge={nudge}
            onNudge={(next, control) =>
              void applyPreference(next, control, FEATURED_PICKS_TOGGLE_ID)
            }
            onRetryRefresh={() => void reload()}
            onUndo={(control) => undo && void runUndo(undo, control)}
            personaName={personaName}
            preferenceBusy={preferenceBusy}
            undo={undo}
          />
        );
        const rail = rest.length ? (
          <MovieRail
            eyebrow={policy.label}
            footer={(movie) => (
              <MovieStateControls
                busy={pendingMovieId === movie.id}
                compact
                controls={RECOMMENDATION_CONTROLS}
                idPrefix={`rail-${movie.id}`}
                onAction={onAction(movie)}
                state={movie.state}
                title={movie.title}
              />
            )}
            movieHref={(movie) => movieHrefFor(movie.id)}
            movies={rest}
            seeAllHref={browseHref}
            title="Next in this ranked set"
          />
        ) : null;

        if (!item || !featured) {
          if (extending) return <PosterSkeleton count={3} />;
          return (
            <>
              <QueueEnd
                browseHref={browseHref}
                decisions={queue.acted.length}
                heldBack={rest.length}
                quickPicksHref={quickPicksHref}
              />
              {status}
              {/* The setting is what caused an all-held-back slot, so it has to
                  stay reachable from the state it produced. */}
              <FeaturedPicksSetting
                busy={preferenceBusy}
                featureWatchlistedTitles={preference.featureWatchlistedTitles}
                onToggle={(next, control) =>
                  void applyPreference(next, control, STATUS_ANCHOR)
                }
              />
              {rail}
            </>
          );
        }
        const featuredIsWatchlisted = isWatchlisted(cardStates[featured.id]);
        return (
          <>
            <FeaturedMovie
              actions={
                <>
                  <MovieStateControls
                    busy={pendingMovieId === featured.id}
                    controls={RECOMMENDATION_CONTROLS}
                    idPrefix={`featured-${featured.id}`}
                    onAction={onAction(featured)}
                    state={featured.state}
                    title={featured.title}
                  />
                  {featuredIsWatchlisted ? (
                    // Beside the state controls rather than inside them: those
                    // three all write, and this one writes nothing. Offered only
                    // for a title the route actually knows is watchlisted —
                    // "unknown state" is not "not watchlisted", and a Skip on a
                    // card with nothing to skip past would mean nothing.
                    <button
                      className="button-quiet discover-skip"
                      id={`featured-${featured.id}-skip`}
                      onClick={(event) => skip(featured, event.currentTarget)}
                      type="button"
                    >
                      {SKIP_LABEL}
                    </button>
                  ) : null}
                </>
              }
              aside={status}
              disclosure={
                <WhyThis
                  featureWatchlistedTitles={preference.featureWatchlistedTitles}
                  item={item}
                  preloadedEvidence={recordedEvidence}
                  requestId={
                    hasResourceData(recommendations) ? recommendations.requestId : null
                  }
                  response={data}
                  userId={userId}
                />
              }
              enterFrom={advanceFrom}
              eyebrow={
                <>
                  <span className={`policy-chip policy-chip-${policy.kind}`}>{policy.label}</span>
                  <span className="policy-rank">Rank {featured.rank ?? 1}</span>
                  {featuredIsWatchlisted ? (
                    // A cue, not a claim about the ranking: it says why a Skip
                    // is on offer next to it and nothing about why the title is
                    // here.
                    <span className="featured-watchlist-cue">{WATCHLIST_CUE}</span>
                  ) : null}
                </>
              }
              href={movieHrefFor(featured.id)}
              // Remounting on the movie is what replays the arrival; without it
              // React keeps the instance and the new card simply appears.
              key={featured.id}
              movie={featured}
            />

            {justWatched ? (
              <JustWatched
                busy={pendingMovieId === justWatched.id}
                libraryHref={libraryHref}
                movie={justWatched}
                onRate={(value, control) =>
                  void commit(justWatched, ratingAction(value), control, "just-watched")
                }
                state={cardStates[justWatched.id] ?? justWatched.state}
              />
            ) : null}

            {/* Under the featured decision and above the rail: it is about the
                slot directly above it, and it is a setting rather than a
                decision, so it must not sit among the three buttons that
                write. */}
            <FeaturedPicksSetting
              busy={preferenceBusy}
              featureWatchlistedTitles={preference.featureWatchlistedTitles}
              onToggle={(next, control) =>
                void applyPreference(next, control, STATUS_ANCHOR)
              }
            />

            {rail}

            <p className="discover-browse-path">
              Nothing here?{" "}
              <Link className="button-quiet" href={browseHref}>
                Browse the whole catalog
              </Link>
            </p>

            <QuickPicksEntry href={quickPicksHref} />
          </>
        );
      }}
    </ResourceRegion>
  );
}

function statusCopy(flow: Flow): string {
  switch (flow.kind) {
    case "saving":
      return flow.message;
    case "refreshing":
      return `${flow.message} Refreshing recommendations…`;
    case "refreshed":
      // Only movement is worth a second sentence. A set that came back
      // identical is the honest outcome of a watchlist press, and the tail
      // that used to say so ("The ranked list is unchanged.") reported on the
      // machinery rather than on the decision — the sentence in front of it
      // already names what was recorded and what is now on screen.
      return flow.moved ? `${flow.message} Recommendations refreshed.` : flow.message;
    case "rated":
    case "skipped":
    case "preference":
      return flow.message;
    case "refresh-failed":
      return `${flow.message} Recommendations could not be refreshed.`;
    case "error":
      return flow.message;
    default:
      return "";
  }
}

function FlowStatus({
  featureWatchlistedTitles,
  flow,
  libraryHref,
  nudge,
  onNudge,
  onRetryRefresh,
  onUndo,
  personaName,
  preferenceBusy,
  undo,
}: {
  featureWatchlistedTitles: boolean;
  flow: Flow;
  libraryHref: string;
  nudge: boolean;
  onNudge: (featureWatchlistedTitles: boolean, control: HTMLElement) => void;
  onRetryRefresh: () => void;
  onUndo: (control: HTMLElement) => void;
  personaName: string;
  preferenceBusy: boolean;
  undo: UndoOffer | null;
}) {
  return (
    <div className="discover-status">
      <p
        aria-live="polite"
        className={`discover-status-copy discover-status-${flow.kind}`}
        id={STATUS_ANCHOR}
        role="status"
        tabIndex={-1}
      >
        {statusCopy(flow) ||
          `Recorded feedback updates ${personaName}'s live history and the next recommendation request.`}
      </p>
      {undo ? (
        // Beside the status rather than in a toast: a toast that stole focus
        // would interrupt the next decision, and one that did not would be
        // unreachable from the keyboard.
        <button
          aria-label={undo.label}
          className="button-quiet discover-undo"
          onClick={(event) => onUndo(event.currentTarget)}
          type="button"
        >
          Undo
        </button>
      ) : null}
      {flow.kind === "rated" ? (
        // The panel carried the way to change a rating, and the panel is gone
        // by the time this sentence is on screen. Watched is `final` on this
        // surface, so the Library is where a star is edited or a watch undone,
        // and the confirmation names it rather than implying it. It is not the
        // last chance to get there — the shell keeps a Library link — but it is
        // the one that is under the viewer's eyes at the moment they might want
        // it.
        <Link className="button-quiet discover-manage" href={libraryHref}>
          Manage in Library
        </Link>
      ) : null}
      {nudge && featureWatchlistedTitles ? (
        // Asked here rather than in a modal or a toast: the question is about
        // the presses the viewer has just been making, it is asked once, and
        // both answers are ordinary buttons a keyboard reaches in one step. It
        // is `group` rather than `alert` because it interrupts nothing — the
        // skip that raised it has already been announced on the line above.
        <div aria-label={NUDGE_QUESTION} className="discover-nudge" role="group">
          <p className="discover-nudge-question">{NUDGE_QUESTION}</p>
          <span className="discover-nudge-actions">
            <button
              aria-disabled={preferenceBusy}
              className="button-secondary"
              onClick={(event) => onNudge(false, event.currentTarget)}
              type="button"
            >
              {NUDGE_CONFIRM}
            </button>
            <button
              aria-disabled={preferenceBusy}
              className="button-quiet"
              onClick={(event) => onNudge(true, event.currentTarget)}
              type="button"
            >
              {NUDGE_DISMISS}
            </button>
          </span>
        </div>
      ) : null}
      {flow.kind === "refresh-failed" ? (
        <ResourceProblem
          failure={flow.failure}
          label="Recommendations"
          onRetry={onRetryRefresh}
        />
      ) : null}
    </div>
  );
}

/**
 * The permanent home of the answer the nudge asks for once.
 *
 * A one-time offer that is the only way to reach a setting is a setting the
 * viewer cannot change their mind about, so the same switch lives here for the
 * whole session — at the foot of the featured section, which is the thing it
 * governs. Deliberately not a menu or a popover: the design contract rules out
 * hover-only information needed to act, and a single labelled switch with a
 * sentence under it needs neither.
 *
 * `aria-pressed` rather than a checkbox, matching the `Watchlist` control it
 * sits below: both are a two-state button whose label says what it does.
 */
function FeaturedPicksSetting({
  busy,
  featureWatchlistedTitles,
  onToggle,
}: {
  busy: boolean;
  featureWatchlistedTitles: boolean;
  onToggle: (featureWatchlistedTitles: boolean, control: HTMLElement) => void;
}) {
  return (
    <section aria-labelledby="featured-picks-heading" className="featured-picks">
      <p className="eyebrow" id="featured-picks-heading">
        {SETTING_EYEBROW}
      </p>
      <button
        aria-busy={busy}
        aria-describedby={FEATURED_PICKS_NOTE_ID}
        aria-disabled={busy}
        aria-pressed={featureWatchlistedTitles}
        className={featureWatchlistedTitles ? "button-primary" : "button-secondary"}
        id={FEATURED_PICKS_TOGGLE_ID}
        onClick={(event) => {
          if (busy) return;
          onToggle(!featureWatchlistedTitles, event.currentTarget);
        }}
        type="button"
      >
        {SETTING_LABEL}
      </button>
      <p className="featured-picks-note" id={FEATURED_PICKS_NOTE_ID}>
        {settingNote(featureWatchlistedTitles)}
      </p>
    </section>
  );
}

/**
 * The end of the queue is a place, not a failure.
 *
 * It names both exits rather than the generic empty state's one, because a
 * viewer who has just decided their way through a ranked set is choosing
 * between browsing and rating a few quickly, not wondering whether something
 * broke.
 */
function QueueEnd({
  browseHref,
  decisions,
  heldBack,
  quickPicksHref,
}: {
  browseHref: string;
  decisions: number;
  /** Titles still in the rail that are not eligible for the featured slot. */
  heldBack: number;
  quickPicksHref: string;
}) {
  // Two different situations wearing one empty state would be the lie: an
  // exhausted queue has nothing left, while a held-back one has titles on the
  // page right now and a setting that explains them. The copy has to be able to
  // tell a viewer which of the two they are looking at.
  const message =
    heldBack > 0
      ? `Every remaining title is on your watchlist, and the Featured picks setting is holding them back from the featured slot. ${heldBack === 1 ? "It is" : "They are"} still listed below.`
      : decisions > 0
        ? `That is every title the ranked set had for now. Your ${decisions} ${decisions === 1 ? "decision" : "decisions"} are recorded, and the next request will be built from them.`
        : "The recommendation API has no more unseen titles for this persona right now.";
  return (
    <EmptyState
      action={
        <>
          <Link className="button-primary" href={browseHref}>
            Browse the catalog
          </Link>
          <QuickPicksEntry
            href={quickPicksHref}
            note="Or keep going one at a time in Quick picks."
          />
        </>
      }
      message={message}
      title={
        heldBack > 0 ? "Nothing new for the featured slot" : "You are through this ranked set"
      }
    />
  );
}

/**
 * A watched title leaves the recommendation set on the next request, so the
 * rating control cannot live on the card that triggered it. Anchoring it here
 * keeps `Watched → Rate` reachable and keeps the honest caveat next to it.
 *
 * The Library link is the other half of that honesty: watched is final on this
 * route by the control-set table, so the place to change it has to be named
 * rather than implied.
 *
 * It is an offer with an end. The panel is unmounted the moment a star commits
 * and the confirmation takes over in the status region, which is what keeps it
 * from becoming a second, permanent surface for a decision that is finished —
 * filled-in stars reading "5 out of 5 recorded" at the foot of a page whose
 * featured slot moved on two decisions ago.
 */
function JustWatched({
  movie,
  state,
  busy,
  libraryHref,
  onRate,
}: {
  movie: MovieCard;
  state: MovieDisplayState;
  busy: boolean;
  libraryHref: string;
  onRate: (value: number | null, control: HTMLElement) => void;
}) {
  return (
    <section aria-labelledby="just-watched-heading" className="just-watched">
      <div>
        <p className="eyebrow">Just marked watched</p>
        <h2 className="section-title" id="just-watched-heading">
          Rate {movie.title}
        </h2>
        <p className="just-watched-manage">
          <Link className="button-quiet" href={libraryHref}>
            Manage in Library
          </Link>
        </p>
      </div>
      <MovieRatingControl
        busy={busy}
        idPrefix={`just-watched-${movie.id}`}
        note="Stars are recorded feedback for this persona. The deployed recommender counts any rating as one observed watch, so a 1 and a 5 are the same learned signal today."
        onRate={onRate}
        rating={state.rating}
        showRecorded
        title={movie.title}
      />
    </section>
  );
}
