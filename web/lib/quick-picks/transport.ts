/**
 * How a Quick Picks decision reaches the durable boundary.
 *
 * Writes go through `lib/movie-state/mutate.ts`, the same canonical path
 * Discover, Browse, detail, and Library use. That is deliberate: a second
 * implementation of "write movie state" is how two surfaces end up disagreeing
 * about what watched means, and this route has the least margin for that.
 *
 * Three things it adds on top, the first two forced by the shape of a
 * recommendation:
 *
 * - **Revisions the queue never saw.** A recommendation item carries no state,
 *   so a first write can only assert revision 0. This session's own commits and
 *   the tab-local relay of states other routes committed are consulted first,
 *   and the shared client turns whatever is still stale into a re-read plus one
 *   replay, so the first press of a card commits instead of being discarded.
 * - **One key per decision.** The deck's reducer creates a fresh `pending`
 *   object for every press, including the re-press after a failure, so the
 *   intent identity a stable idempotency key hangs off has to be recognised
 *   here: same movie, same action, same rating means the same decision, and it
 *   keeps the key it was first minted under until it commits.
 * - **The queue read is injected.** The live route hands over a server action
 *   because the recommendations read and the audit that explains it have to be
 *   sequenced server-side; the fixture harness hands over recorded data.
 */

import type { MovieState, RecommendationResponse } from "@/lib/api";
import {
  quickPickHttpRequest,
  type QuickPickCommitRequest,
} from "@/lib/quick-picks/contract";
import type { QuickPickEvidenceMap } from "@/lib/quick-picks/evidence";
import {
  readCommittedStates,
  recordCommittedState,
} from "@/lib/movie-state/committed-store";
import { createBffMovieStateClient } from "@/lib/movie-state/client";
import {
  newIdempotencyKey,
  type MovieStateMutationResult,
} from "@/lib/movie-state/mutate";
import { MOVIE_DETAIL } from "@/lib/resources/definitions";
import { readBffResource } from "@/lib/resources/browser";
import { hasResourceData, type ResourceState } from "@/lib/resources/state";

export type QuickPickCommitOutcome =
  | { ok: true; state: MovieState }
  | {
      ok: false;
      message: string;
      /** A revision conflict; the canonical state has been re-read. */
      conflict?: boolean;
      /**
       * The API declined the transition itself. Nothing was written and
       * nothing was re-read, so a second press cannot change the answer.
       */
      refused?: boolean;
    };

/**
 * The queue and the audit that explains it travel together: they are read in
 * one server round trip so the evidence can be matched to the queue by
 * correlation ID instead of by hoping the two reads lined up.
 */
export type QuickPickQueuePayload = {
  queue: ResourceState<RecommendationResponse>;
  evidence: QuickPickEvidenceMap;
};

export type QuickPickTransport = {
  commit(request: QuickPickCommitRequest): Promise<QuickPickCommitOutcome>;
  refresh(): Promise<QuickPickQueuePayload>;
  /** `null` whenever the seed title cannot be established truthfully. */
  resolveSeedTitle(movieId: number): Promise<string | null>;
};

export function quickPickFailureCopy(
  result: Extract<MovieStateMutationResult, { status: "conflict" | "refused" | "failed" }>,
): string {
  if (result.status === "conflict") {
    return "That title changed somewhere else before this saved. Its current state has been loaded; try again.";
  }
  if (result.status === "refused") {
    // The API's own sentence, because it names the rule this decision would
    // have broken and no copy written here could say it more precisely. It is
    // deliberately not offered as something to try again: nothing changed
    // anywhere, so a second press asks the same rule the same question.
    const reason = result.detail.trim();
    return `That decision was not recorded. ${/[.!?]$/.test(reason) ? reason : `${reason}.`}`;
  }
  switch (result.failure.status) {
    case "auth-expired":
      return "Your session expired before this saved. Sign in again to keep deciding.";
    case "forbidden":
      return "This session is not allowed to change state for this persona.";
    case "not-found":
      return "That title is no longer in the catalog for this persona.";
    default:
      return `The recommendation API did not save that decision. Request ${result.failure.requestId}.`;
  }
}

/** Two requests describe the same decision when they would write the same thing. */
function sameDecision(
  left: QuickPickCommitRequest,
  right: QuickPickCommitRequest,
): boolean {
  return (
    left.movieId === right.movieId &&
    left.action === right.action &&
    left.rating === right.rating
  );
}

export function createLiveQuickPickTransport(options: {
  userId: number;
  loadQueue: () => Promise<QuickPickQueuePayload>;
  fetchImpl?: typeof fetch;
  /** Injectable so the revision relay is testable outside a browser. */
  sessionStore?: Pick<Storage, "getItem" | "setItem" | "removeItem">;
}): QuickPickTransport {
  const { userId } = options;
  const revisions = new Map<number, number>();
  const client = createBffMovieStateClient(options.fetchImpl);
  // The decision currently being attempted and the key it was minted under.
  // Cleared on a commit, so a repeat of the same decision later is a new one.
  let intent: { request: QuickPickCommitRequest; key: string } | null = null;

  function store() {
    return (
      options.sessionStore ??
      (typeof window === "undefined" ? undefined : window.sessionStorage)
    );
  }

  /**
   * The revision the next write asserts. This session's own commits come
   * first, then the tab-local relay of what other routes committed, and only
   * then the canonical "no state yet" value. Nothing here invents a revision.
   */
  function knownRevision(movieId: number): number {
    const own = revisions.get(movieId);
    if (own !== undefined) return own;
    const relay = store();
    if (!relay) return 0;
    return readCommittedStates(relay, userId).get(movieId)?.revision ?? 0;
  }

  /**
   * A committed write relays itself — recording the canonical answer is part of
   * the shared write path — so this only has to remember the revision the next
   * decision asserts.
   */
  function rememberRevision(state: MovieState) {
    revisions.set(state.movie_id, state.revision);
  }

  /** A canonical *read* has nothing behind it, so it relays on its own. */
  function adopt(state: MovieState) {
    rememberRevision(state);
    const relay = store();
    if (relay) recordCommittedState(relay, userId, state);
  }

  return {
    async commit(request) {
      let http;
      try {
        http = quickPickHttpRequest(request);
      } catch (error) {
        // A rating the API would reject never leaves the browser.
        return {
          ok: false,
          message: error instanceof Error ? error.message : "That decision is not valid.",
        };
      }
      const key =
        intent && sameDecision(intent.request, request)
          ? intent.key
          : newIdempotencyKey();
      intent = { request, key };

      const result = await client.mutate({
        userId,
        movieId: request.movieId,
        resource: http.resource,
        method: http.method,
        rating: http.body?.rating,
        // The machine reports what it observed; `null` means a queue card that
        // never carried a state, and the relay answers for it.
        expectedRevision: http.expectedRevision ?? knownRevision(request.movieId),
        idempotencyKey: key,
        fetchImpl: options.fetchImpl,
        // Keeps the relay testable outside a browser: one sink, chosen here.
        store: store() ?? null,
      });

      if (result.status === "committed") {
        intent = null;
        rememberRevision(result.state);
        return { ok: true, state: result.state };
      }
      if (result.status === "conflict") {
        // The client already read the canonical record and replayed against
        // it, so this is a real conflict. Adopt what it read back — the key
        // belongs to a revision that is gone, so the next press is a new
        // decision against the state now on file.
        if (result.canonical) adopt(result.canonical);
        intent = null;
        return { ok: false, message: quickPickFailureCopy(result), conflict: true };
      }
      if (result.status === "refused") {
        // A rule rather than a race, so there is no canonical record to adopt
        // and no revision to move on from — the write never happened. The
        // intent keeps its key: no feedback event was written, so a re-press is
        // still the same decision rather than a second one.
        return { ok: false, message: quickPickFailureCopy(result), refused: true };
      }
      return { ok: false, message: quickPickFailureCopy(result) };
    },

    refresh: options.loadQueue,

    async resolveSeedTitle(movieId) {
      // Evidence is progressive disclosure; losing a seed title degrades the
      // sentence to its source rather than failing the card.
      const detail = await readBffResource(
        MOVIE_DETAIL,
        `/api/users/${userId}/movies/${movieId}`,
        { fetchImpl: options.fetchImpl },
      );
      return hasResourceData(detail) ? detail.data.item.title : null;
    },
  };
}
