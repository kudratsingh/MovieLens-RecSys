"use client";

/**
 * One movie's durable state, reconciled from what the API committed.
 *
 * The split this hook maintains is the whole point of it: what the viewer
 * *sees* may run ahead of the server, but what the code *believes* never does.
 * The optimistic value lives in `display` and is thrown away on either
 * outcome; `state` — the canonical record, revision included — is only ever
 * replaced by a response the API committed. Nothing here invents a revision,
 * so the next write always sends a number the server issued.
 *
 * A failure therefore rolls back by construction: dropping the pending patch
 * restores the last committed truth. Focus goes back to the control that
 * started the action, because the alternative is a screen-reader or keyboard
 * viewer being dropped at the top of the document by a failed save.
 */

import { useCallback, useRef, useState } from "react";

import type { MovieState } from "@/lib/api";
import { recordCommittedState } from "@/lib/movie-state/committed-store";
import {
  mutateMovieState,
  type MovieStateResource,
} from "@/lib/movie-state/mutate";
import type { ResourceFailureReason } from "@/lib/resources/state";

export type MovieStateAction =
  | { resource: "watchlist"; method: "PUT" | "DELETE" }
  | { resource: "watched"; method: "PUT" | "DELETE" }
  | { resource: "dismissal"; method: "PUT" | "DELETE" }
  | { resource: "rating"; method: "PUT"; rating: number }
  | { resource: "rating"; method: "DELETE" };

export type MovieDisplayState = {
  watched: boolean;
  watchlisted: boolean;
  dismissed: boolean;
  rating: number | null;
};

export type MovieStateMessage = { tone: "ok" | "error"; text: string };

function displayOf(state: MovieState | null): MovieDisplayState {
  return {
    watched: Boolean(state?.watched_at),
    watchlisted: Boolean(state?.watchlisted_at),
    dismissed: Boolean(state?.dismissed_at),
    rating: state?.rating ?? null,
  };
}

function optimisticPatch(action: MovieStateAction): Partial<MovieDisplayState> {
  switch (action.resource) {
    case "watchlist":
      return { watchlisted: action.method === "PUT" };
    case "dismissal":
      return { dismissed: action.method === "PUT" };
    case "watched":
      return action.method === "PUT"
        ? { watched: true }
        : // Removing history also removes the rating that implied it.
          { watched: false, rating: null };
    case "rating":
      return action.method === "PUT"
        ? { rating: action.rating, watched: true }
        : { rating: null };
  }
}

/**
 * Copy is written against what the deployed system actually does. A rating is
 * an observed positive interaction under ADR 0002, not a graded signal, and a
 * watchlist entry changes no model input at all.
 */
function successMessage(action: MovieStateAction, title: string): string {
  switch (action.resource) {
    case "watchlist":
      return action.method === "PUT"
        ? `Saved ${title} to your watchlist. Watchlist is organisational and changes no recommendation input.`
        : `Removed ${title} from your watchlist.`;
    case "watched":
      return action.method === "PUT"
        ? `Marked ${title} as watched. It now counts in live history and unseen filtering.`
        : `Removed ${title} from history, including the watched interaction.`;
    case "dismissal":
      return action.method === "PUT"
        ? `Dismissed ${title}. It is excluded from recommendations and can be undone.`
        : `Restored ${title}. It can be recommended again.`;
    case "rating":
      return action.method === "PUT"
        ? `Saved ${action.rating} stars for ${title}. A rating records watched history; star magnitude is not a graded model signal.`
        : `Removed your rating for ${title}. It stays in watched history.`;
  }
}

export function useCanonicalMovieState(options: {
  userId: number;
  movieId: number;
  title: string;
  initialState: MovieState | null;
  onCommitted?: (state: MovieState) => void;
}) {
  const { userId, movieId, title, onCommitted } = options;
  const [state, setState] = useState<MovieState | null>(options.initialState);
  const [optimistic, setOptimistic] = useState<Partial<MovieDisplayState> | null>(
    null,
  );
  const [pending, setPending] = useState<MovieStateResource | null>(null);
  const [message, setMessage] = useState<MovieStateMessage | null>(null);
  const inFlight = useRef(false);

  const run = useCallback(
    async (action: MovieStateAction, trigger?: HTMLElement | null) => {
      // Serialising writes keeps `expected_revision` meaningful: two concurrent
      // writes would both send the revision we started from and one would lose.
      if (inFlight.current) return;
      inFlight.current = true;
      setPending(action.resource);
      setOptimistic(optimisticPatch(action));
      setMessage(null);

      const result = await mutateMovieState({
        userId,
        movieId,
        resource: action.resource,
        method: action.method,
        rating: action.resource === "rating" && action.method === "PUT" ? action.rating : undefined,
        expectedRevision: state?.revision ?? 0,
      });

      setOptimistic(null);
      setPending(null);
      inFlight.current = false;

      if (result.status === "committed") {
        setState(result.state);
        setMessage({ tone: "ok", text: successMessage(action, title) });
        if (typeof window !== "undefined") {
          recordCommittedState(window.sessionStorage, userId, result.state);
        }
        onCommitted?.(result.state);
        return;
      }

      setMessage({ tone: "error", text: failureMessage(result, title) });
      // The pending patch is already gone, so the control has rolled back to
      // the last committed value; put the viewer back on it.
      trigger?.focus();
    },
    [movieId, onCommitted, state?.revision, title, userId],
  );

  return {
    state,
    display: { ...displayOf(state), ...(optimistic ?? {}) } as MovieDisplayState,
    pending,
    message,
    run,
  };
}

function failureMessage(
  result: Extract<
    Awaited<ReturnType<typeof mutateMovieState>>,
    { status: "conflict" | "failed" }
  >,
  title: string,
): string {
  if (result.status === "conflict") {
    return `${title} changed somewhere else before this saved. Reload to see the current state.`;
  }
  switch (result.failure.status) {
    case "auth-expired":
      return `Your session expired before this saved. Sign in again to change ${title}.`;
    case "forbidden":
      return `This session is not allowed to change state for ${title}.`;
    case "not-found":
      return `${title} is no longer in the catalog for this persona.`;
    default: {
      const cause = TRANSPORT_CAUSE[result.failure.reason];
      // Without a cause we can state plainly, the request ID is the only
      // useful thing to hand over — it is what ties this to an audit row.
      return cause
        ? `${cause} Nothing was saved for ${title}.`
        : `Nothing was saved for ${title}. Request ${result.failure.requestId}.`;
    }
  }
}

const TRANSPORT_CAUSE: Partial<Record<ResourceFailureReason, string>> = {
  timeout: "The recommendation API did not answer.",
  network: "The recommendation API could not be reached.",
  server: "The recommendation API returned an error.",
  "rate-limited": "Too many requests reached the recommendation API.",
};
