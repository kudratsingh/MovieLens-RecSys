/**
 * How a Quick Picks decision reaches the durable boundary.
 *
 * Writes go through `lib/movie-state/mutate.ts`, the same canonical path
 * Discover, Browse, detail, and Library use. That is deliberate: a second
 * implementation of "write movie state" is how two surfaces end up disagreeing
 * about what watched means, and this route has the least margin for that.
 *
 * Two things it adds on top, both forced by the shape of a recommendation:
 *
 * - **Revisions the queue never saw.** A recommendation item carries no state,
 *   so a first write can only assert revision 0. This session's own commits and
 *   the tab-local relay of states other routes committed are consulted first,
 *   and a `409` triggers a re-read that turns the conflict into a correction.
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
import {
  mutateMovieState,
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
  result: Extract<MovieStateMutationResult, { status: "conflict" | "failed" }>,
): string {
  if (result.status === "conflict") {
    return "That title changed somewhere else before this saved. Its current state has been loaded; try again.";
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

export function createLiveQuickPickTransport(options: {
  userId: number;
  loadQueue: () => Promise<QuickPickQueuePayload>;
  fetchImpl?: typeof fetch;
  /** Injectable so the revision relay is testable outside a browser. */
  sessionStore?: Pick<Storage, "getItem" | "setItem" | "removeItem">;
}): QuickPickTransport {
  const { userId } = options;
  const revisions = new Map<number, number>();

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

  function adopt(state: MovieState) {
    revisions.set(state.movie_id, state.revision);
    const relay = store();
    if (relay) recordCommittedState(relay, userId, state);
  }

  async function resync(movieId: number) {
    const detail = await readBffResource(
      MOVIE_DETAIL,
      `/api/users/${userId}/movies/${movieId}`,
      { fetchImpl: options.fetchImpl },
    );
    if (hasResourceData(detail) && detail.data.item.state) {
      adopt(detail.data.item.state);
    }
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
      const result = await mutateMovieState({
        userId,
        movieId: request.movieId,
        resource: http.resource,
        method: http.method,
        rating: http.body?.rating,
        // The machine reports what it observed; `null` means a queue card that
        // never carried a state, and the relay answers for it.
        expectedRevision: http.expectedRevision ?? knownRevision(request.movieId),
        fetchImpl: options.fetchImpl,
      });

      if (result.status === "committed") {
        adopt(result.state);
        return { ok: true, state: result.state };
      }
      if (result.status === "conflict") {
        await resync(request.movieId);
        return { ok: false, message: quickPickFailureCopy(result), conflict: true };
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
