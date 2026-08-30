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
 *
 * Two behaviours are worth naming because they are what a shared write path
 * buys. A conflict is *corrected* rather than reported and abandoned: the
 * canonical record is read back, adopted, and the next click asserts a revision
 * the server issued. And the idempotency key belongs to the intent, so pressing
 * the same control again after a failure replays the original attempt instead
 * of risking a second feedback event for one decision.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { MovieState } from "@/lib/api";
import {
  applyActionToDisplay,
  displayState,
  sameAction,
  type MovieDisplayState,
  type MovieStateAction,
  type MovieStateResource,
} from "@/lib/movie-state/actions";
import {
  movieStateAnnouncement,
  type MovieStateVoice,
} from "@/lib/movie-state/announce";
import { bffMovieStateClient, type MovieStateClient } from "@/lib/movie-state/client";
import { restoreFocus, type FocusTarget } from "@/lib/movie-state/focus";
import { newIdempotencyKey } from "@/lib/movie-state/mutate";

/**
 * `note` is the third tone because a refused transition is neither of the other
 * two: nothing broke, and nothing was saved. It is announced politely and
 * rendered as a note rather than as a failure, which is also why it does not
 * offer a retry.
 */
export type MovieStateMessage = { tone: "ok" | "note" | "error"; text: string };

export type MovieStateOptions = {
  userId: number;
  movieId: number;
  title: string;
  initialState: MovieState | null;
  /** Selects the announcement register for this surface. */
  voice?: MovieStateVoice;
  persona?: string;
  client?: MovieStateClient;
  onCommitted?: (state: MovieState) => void;
};

export function useMovieState(options: MovieStateOptions) {
  const {
    userId,
    movieId,
    title,
    voice = "detail",
    persona,
    client = bffMovieStateClient,
    onCommitted,
  } = options;

  const [state, setState] = useState<MovieState | null>(options.initialState);
  const [optimistic, setOptimistic] = useState<MovieDisplayState | null>(null);
  const [pending, setPending] = useState<MovieStateResource | null>(null);
  const [message, setMessage] = useState<MovieStateMessage | null>(null);

  // Mirrors `state` for the callback below, which must never read a revision
  // from a stale closure. Only `adopt` writes either one.
  const stateRef = useRef(options.initialState);
  const inFlight = useRef(false);
  // The intent currently being attempted, with the key it was minted under.
  const intent = useRef<{ action: MovieStateAction; key: string } | null>(null);
  // Which movie the state above belongs to.
  const boundTo = useRef(movieId);
  const incoming = options.initialState;

  /*
   * Two things this hook used to ignore for the lifetime of an instance: the
   * movie it is pointed at, and a fresher server render of the same movie.
   *
   * Every caller keys its control by movie id today, so the rebind never fires
   * in the product as it stands. It is here because the failure if one ever
   * stops doing that is silent and expensive — the hook would assert movie A's
   * revision against movie B, and the viewer would get a conflict on a movie
   * they never touched.
   */
  useEffect(() => {
    if (boundTo.current !== movieId) {
      boundTo.current = movieId;
      stateRef.current = incoming;
      intent.current = null;
      inFlight.current = false;
      setState(incoming);
      setOptimistic(null);
      setPending(null);
      setMessage(null);
      return;
    }
    // A strictly newer render of the same movie — another surface committed and
    // the route re-read — keeps the next `expected_revision` current. A lower
    // revision is a stale render and is ignored, because what this hook holds
    // was committed by the API and is the fresher of the two.
    if (incoming && incoming.revision > (stateRef.current?.revision ?? -1)) {
      stateRef.current = incoming;
      setState(incoming);
    }
  }, [incoming, movieId]);

  const adopt = useCallback(
    (committed: MovieState) => {
      setState(committed);
      stateRef.current = committed;
      onCommitted?.(committed);
    },
    [onCommitted],
  );

  const run = useCallback(
    async (
      action: MovieStateAction,
      trigger?: FocusTarget,
      focusFallbacks: FocusTarget[] = [],
    ) => {
      // Serialising writes keeps `expected_revision` meaningful: two concurrent
      // writes would both send the revision we started from and one would lose.
      if (inFlight.current) return;
      inFlight.current = true;

      // Re-pressing the control after a failure is one intent, not two, so it
      // carries the original key and the API replays rather than re-applies.
      const previous = intent.current;
      const key =
        previous && sameAction(previous.action, action)
          ? previous.key
          : newIdempotencyKey();
      intent.current = { action, key };

      setPending(action.resource);
      setOptimistic(applyActionToDisplay(displayState(stateRef.current), action));
      setMessage(null);

      const result = await client.mutate({
        userId,
        movieId,
        resource: action.resource,
        method: action.method,
        rating:
          action.resource === "rating" && action.method === "PUT"
            ? action.rating
            : undefined,
        expectedRevision: stateRef.current?.revision ?? 0,
        idempotencyKey: key,
      });

      // Repointed at another movie mid-write. The reset above already cleared
      // the pending frame; adopting this response would put one movie's
      // committed state, revision and all, onto a different movie.
      if (boundTo.current !== movieId) return;

      setOptimistic(null);
      setPending(null);
      inFlight.current = false;

      if (result.status === "committed") {
        intent.current = null;
        adopt(result.state);
        setMessage({
          tone: "ok",
          text: movieStateAnnouncement({ kind: "committed", action }, {
            title,
            voice,
            persona,
          }),
        });
        return;
      }

      if (result.status === "conflict") {
        // Somebody committed first. Show what is actually stored rather than
        // guessing which side won, and let the next click assert its revision.
        const canonical = await client.readState(userId, movieId);
        if (canonical) adopt(canonical);
        // The stored key belongs to a revision that is gone; a retry is a new
        // intent against the state that was just read back.
        intent.current = null;
        setMessage({
          tone: "error",
          text: movieStateAnnouncement({ kind: "conflict" }, { title, voice, persona }),
        });
      } else if (result.status === "refused") {
        // A rule, not a race — so no replay. But the rule is about state, and
        // this control asserted a picture of that state the API just
        // contradicted, so the write path read the record back: adopt it, and
        // the control stops offering an action that cannot succeed. The intent
        // keeps its key — no feedback event was written, so pressing again is
        // still the same decision rather than a second one.
        if (result.canonical) adopt(result.canonical);
        setMessage({
          tone: "note",
          text: movieStateAnnouncement(
            { kind: "refused", detail: result.detail, corrected: result.corrected },
            { title, voice, persona },
          ),
        });
      } else {
        setMessage({
          tone: "error",
          text: movieStateAnnouncement({ kind: "failed", failure: result.failure }, {
            title,
            voice,
            persona,
          }),
        });
      }

      // The pending patch is already gone, so the control has rolled back to
      // the last committed value; put the viewer back on it.
      restoreFocus(trigger, ...focusFallbacks);
    },
    [adopt, client, movieId, persona, title, userId, voice],
  );

  return {
    state,
    display: optimistic ?? displayState(state),
    pending,
    message,
    run,
  };
}
