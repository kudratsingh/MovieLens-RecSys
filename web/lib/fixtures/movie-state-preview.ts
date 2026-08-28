/**
 * An in-memory `MovieStateClient` for the recorded `/ui-preview` surfaces.
 *
 * The seam exists for exactly this — a live route never receives anything but
 * `bffMovieStateClient` — and the preview needs it because two of the states
 * the evidence matrix has to show are the *result* of a write: the rating chip
 * a commit collapses into, and the reopened row behind it. Without a client
 * that answers, pressing a star in the preview reaches a BFF that is not there,
 * rolls back, and the preview can only ever show the idle control.
 *
 * It is not a mock of the API. It applies ADR 0012's transitions by calling the
 * same `applyActionToDisplay` every live surface uses, so a preview screenshot
 * cannot show a combination the product would never commit — a watchlist entry
 * standing after a movie is marked watched, say, which is exactly the drift the
 * shared transition table was introduced to end. The only thing written here is
 * the mapping from that projection back to timestamps, plus a revision that
 * increments so the control's own `expected_revision` bookkeeping is exercised.
 */

import type { MovieState } from "@/lib/api";
import {
  applyActionToDisplay,
  displayState,
  type MovieStateAction,
} from "@/lib/movie-state/actions";
import type { MovieStateClient } from "@/lib/movie-state/client";
import type { MovieStateMutationInput } from "@/lib/movie-state/mutate";

export function createPreviewMovieStateClient(
  seed: MovieState | null,
  options: { tenantId?: string } = {},
): MovieStateClient {
  let current = seed;

  function commit(input: MovieStateMutationInput): MovieState {
    const now = new Date().toISOString();
    const base: MovieState = current ?? {
      tenant_id: options.tenantId ?? "demo",
      user_id: input.userId,
      movie_id: input.movieId,
      rating: null,
      rating_updated_at: null,
      watched_at: null,
      watchlisted_at: null,
      dismissed_at: null,
      revision: 0,
      updated_at: now,
    };
    const next = applyActionToDisplay(displayState(current), toAction(input));

    current = {
      ...base,
      rating: next.rating,
      rating_updated_at: next.rating === null ? null : now,
      // A flag that stays set keeps the time it was first set: "watched since"
      // is the fact, and re-recording it on every edit would lose it.
      watched_at: next.watched ? (base.watched_at ?? now) : null,
      watchlisted_at: next.watchlisted ? (base.watchlisted_at ?? now) : null,
      dismissed_at: next.dismissed ? (base.dismissed_at ?? now) : null,
      revision: base.revision + 1,
      updated_at: now,
    };
    return current;
  }

  return {
    async mutate(input) {
      return {
        status: "committed",
        state: commit(input),
        replayed: false,
        outcome: "changed",
        requestId: "recorded-contract-fixture",
      };
    },
    async readState() {
      return current;
    },
  };
}

function toAction(input: MovieStateMutationInput): MovieStateAction {
  if (input.resource === "rating") {
    return input.method === "PUT"
      ? { resource: "rating", method: "PUT", rating: input.rating ?? 0 }
      : { resource: "rating", method: "DELETE" };
  }
  return { resource: input.resource, method: input.method };
}
