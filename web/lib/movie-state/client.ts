/**
 * The one seam every surface writes movie state through.
 *
 * Two operations, because a canonical write needs both of them: commit the
 * change, and — when the API answers `409` — read back what is actually stored
 * so the control can correct itself instead of asking the viewer to reload.
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

export function createBffMovieStateClient(
  fetchImpl?: typeof fetch,
): MovieStateClient {
  return {
    mutate(input) {
      return mutateMovieState({ ...input, fetchImpl: input.fetchImpl ?? fetchImpl });
    },

    async readState(userId, movieId) {
      const state = await readBffResource(
        MOVIE_DETAIL,
        `/api/users/${userId}/movies/${movieId}`,
        { fetchImpl },
      );
      return hasResourceData(state) ? (state.data.item.state ?? null) : null;
    },
  };
}

export const bffMovieStateClient: MovieStateClient = createBffMovieStateClient();
