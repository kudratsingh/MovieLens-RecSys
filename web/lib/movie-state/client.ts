/**
 * The one seam every surface writes movie state through.
 *
 * Two operations, because a canonical write needs both of them: commit the
 * change, and — when the API answers `409` — read back what is actually stored
 * so the control can correct itself instead of asking the viewer to reload.
 *
 * The recovery is completed here rather than in each caller. A recommendation
 * carries no per-item state, so the first press on a Discover or Quick Picks
 * card can only assert `expected_revision=0`; any title that has ever been
 * written and reverted — which the product's own undo affordances do routinely
 * — sits at a higher revision and refuses that assertion. Three surfaces had
 * each built the first half of the answer (re-read the canonical record) and
 * stopped there, so the viewer's first press was silently discarded and only
 * the second one committed. Reading the record and *replaying the same intent
 * against it* is one operation, and it belongs on the seam that both halves
 * already live on.
 *
 * Replaying is safe in both directions, which is what makes it a fix rather
 * than a gamble:
 *
 * - The revision conflict is raised before any feedback event is written, so
 *   after a `409` the idempotency key is unused and the replay is the first
 *   write. If instead the original attempt did commit and only its response
 *   was lost, the API finds the key and replays the stored result rather than
 *   applying it twice — which is the behaviour we want either way. That only
 *   holds if both attempts carry the *same* key, so the key is minted here
 *   when the caller did not bind one.
 * - Every transition in ADR 0012's table is absolute — a `PUT`/`DELETE` on a
 *   named sub-resource, never a relative change — so applying it against the
 *   canonical revision expresses the same intent the viewer pressed.
 * - Exactly one replay, never a loop. A second `409` is a genuine conflict and
 *   is reported as one, with the canonical record attached so the caller can
 *   still correct its control.
 *
 * It is an interface rather than a bare function so the recorded `/ui-preview`
 * surfaces can supply a working in-memory implementation. That is the only
 * reason it exists: a live route never receives anything but
 * `bffMovieStateClient`, and this module imports no fixture.
 *
 * `fetchImpl` is optional rather than defaulted so the global `fetch` is
 * resolved when a request is made, not when the module is loaded. A default
 * parameter would capture whatever `fetch` existed at import time, which is a
 * real difference the moment anything — a test, an instrumented runtime —
 * replaces it afterwards.
 */

import type { MovieState } from "@/lib/api";
import {
  mutateMovieState,
  newIdempotencyKey,
  type MovieStateMutationInput,
  type MovieStateMutationResult,
} from "@/lib/movie-state/mutate";
import { readBffResource } from "@/lib/resources/browser";
import { MOVIE_DETAIL } from "@/lib/resources/definitions";
import { hasResourceData } from "@/lib/resources/state";

export type MovieStateClient = {
  mutate(input: MovieStateMutationInput): Promise<MovieStateMutationResult>;
  /** Used to recover a control after the API reports a revision conflict. */
  readState(userId: number, movieId: number): Promise<MovieState | null>;
};

async function readCanonicalState(
  fetchImpl: typeof fetch | undefined,
  userId: number,
  movieId: number,
): Promise<MovieState | null> {
  const state = await readBffResource(
    MOVIE_DETAIL,
    `/api/users/${userId}/movies/${movieId}`,
    { fetchImpl },
  );
  return hasResourceData(state) ? (state.data.item.state ?? null) : null;
}

export function createBffMovieStateClient(
  fetchImpl?: typeof fetch,
): MovieStateClient {
  return {
    async mutate(input) {
      const fetchForCall = input.fetchImpl ?? fetchImpl;
      // Bound to the intent, not to the attempt: the replay below has to carry
      // the key the first attempt used or the API cannot tell one decision
      // pressed twice from two decisions.
      const idempotencyKey = input.idempotencyKey ?? newIdempotencyKey();
      const attempt = { ...input, fetchImpl: fetchForCall, idempotencyKey };

      const first = await mutateMovieState(attempt);
      // `refused` leaves here untouched, and that is the point of the split:
      // a transition the API will not perform is not a stale render, so it
      // gets no canonical re-read and no replay. Retrying it would only ask
      // the same rule the same question.
      if (first.status !== "conflict") return first;

      const canonical = await readCanonicalState(
        fetchForCall,
        input.userId,
        input.movieId,
      );
      // Without the canonical record there is nothing to replay against, so
      // the conflict stands and the caller reports it.
      if (!canonical) return { ...first, canonical: null };
      // Replaying the assertion that was just refused would only produce the
      // same answer; that is a genuine conflict, not a stale render.
      if (canonical.revision === input.expectedRevision) return { ...first, canonical };

      const replay = await mutateMovieState({
        ...attempt,
        expectedRevision: canonical.revision,
      });
      return replay.status === "conflict" ? { ...replay, canonical } : replay;
    },

    readState(userId, movieId) {
      return readCanonicalState(fetchImpl, userId, movieId);
    },
  };
}

export const bffMovieStateClient: MovieStateClient = createBffMovieStateClient();
