"use client";

/**
 * The canonical watched / rating / watchlist / dismissal controls.
 *
 * Bundle 4's `StateControls` is the preview-only twin of this: same shape,
 * local toggles, no writes. This one talks to the committed state path, so it
 * carries the parts that only matter once a write is real — a pending control
 * that cannot be double-submitted, an announcement for each outcome, and a
 * confirmation on the one action that destroys a signal rather than adding one.
 *
 * The copy is held to what the deployed system does. Watchlist changes no
 * model input. A star records watched history but is not a graded signal.
 * Dismissal excludes a title and is undoable; it is not a learned negative.
 */

import { useState } from "react";

import { Icon } from "@/components/ui/icons";
import {
  useCanonicalMovieState,
  type MovieStateAction,
} from "@/components/movie/use-canonical-movie-state";
import type { MovieState } from "@/lib/api";
import "./canonical-state-controls.css";

const STARS = [1, 2, 3, 4, 5];

export function CanonicalStateControls({
  userId,
  movieId,
  title,
  initialState,
  onCommitted,
}: {
  userId: number;
  movieId: number;
  title: string;
  initialState: MovieState | null;
  onCommitted?: (state: MovieState) => void;
}) {
  const { display, pending, message, run } = useCanonicalMovieState({
    userId,
    movieId,
    title,
    initialState,
    onCommitted,
  });
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);

  const busy = pending !== null;

  function act(action: MovieStateAction) {
    return (event: React.MouseEvent<HTMLButtonElement>) => {
      if (busy) return;
      // Held as a DOM reference on purpose: React clears `currentTarget` on the
      // synthetic event once the handler returns, and focus recovery happens
      // after the request resolves.
      const trigger = event.currentTarget;
      setConfirmingRemoval(false);
      void run(action, trigger);
    };
  }

  return (
    <div className="canonical-state">
      <div className="canonical-state-primary">
        <button
          aria-busy={pending === "watchlist"}
          aria-disabled={busy}
          aria-pressed={display.watchlisted}
          className={display.watchlisted ? "button-primary" : "button-secondary"}
          onClick={act({
            resource: "watchlist",
            method: display.watchlisted ? "DELETE" : "PUT",
          })}
          type="button"
        >
          <Icon name="bookmark" />
          {display.watchlisted ? "In watchlist" : "Watchlist"}
        </button>

        {display.watched ? (
          confirmingRemoval ? (
            <span className="canonical-confirm">
              <button
                aria-disabled={busy}
                className="button-secondary"
                onClick={act({ resource: "watched", method: "DELETE" })}
                type="button"
              >
                Confirm removal
              </button>
              <button
                className="button-quiet"
                onClick={() => setConfirmingRemoval(false)}
                type="button"
              >
                Keep it
              </button>
            </span>
          ) : (
            <button
              aria-pressed
              className="button-secondary"
              onClick={() => setConfirmingRemoval(true)}
              type="button"
            >
              <Icon name="check" />
              Watched · remove
            </button>
          )
        ) : (
          <button
            aria-busy={pending === "watched"}
            aria-disabled={busy}
            aria-pressed={false}
            className="button-secondary"
            onClick={act({ resource: "watched", method: "PUT" })}
            type="button"
          >
            <Icon name="check" />
            Mark watched
          </button>
        )}

        <button
          aria-disabled={busy}
          className="button-quiet"
          onClick={act({
            resource: "dismissal",
            method: display.dismissed ? "DELETE" : "PUT",
          })}
          type="button"
        >
          {display.dismissed ? "Undo dismissal" : "Not for me"}
        </button>
      </div>

      {confirmingRemoval ? (
        <p className="canonical-state-warning" role="status">
          Removing {title} from history also removes the watched interaction the
          recommender observed. Your rating goes with it.
        </p>
      ) : null}

      {/*
        The rating panel is always reachable, not gated behind "Mark watched".
        Setting a rating *is* recording a watched interaction on the API side,
        so hiding the stars until watched would make the shorter path to the
        same committed state unreachable — and would leave a viewer who wants
        to rate something clicking two buttons to say one thing. Watchlist
        stays the primary action for an unseen movie; this sits below it and
        says plainly what a star commits to.
      */}
      <fieldset className="canonical-rating">
        <legend>Your rating</legend>
        <div className="canonical-rating-stars">
          {STARS.map((value) => (
            <button
              aria-disabled={busy}
              aria-label={`${value} ${value === 1 ? "star" : "stars"} for ${title}`}
              aria-pressed={display.rating === value}
              className={
                display.rating !== null && value <= display.rating
                  ? "rating-active"
                  : undefined
              }
              key={value}
              onClick={act({ resource: "rating", method: "PUT", rating: value })}
              type="button"
            >
              <Icon name="star" />
            </button>
          ))}
          {display.rating !== null ? (
            <button
              aria-disabled={busy}
              className="button-quiet"
              onClick={act({ resource: "rating", method: "DELETE" })}
              type="button"
            >
              Clear rating
            </button>
          ) : null}
        </div>
        <p className="canonical-rating-note">
          {display.watched
            ? "Star magnitude is display feedback today, not a graded training signal."
            : "Rating this records it as watched history. Star magnitude is display feedback today, not a graded training signal."}
        </p>
      </fieldset>

      {/*
        Two regions rather than one whose role flips: a live region has to be
        in the accessibility tree before its text changes, and swapping the
        role between renders is exactly how announcements get dropped.
      */}
      <p className="canonical-state-message" role="status">
        {message?.tone === "ok" ? message.text : ""}
      </p>
      <p className="canonical-state-message is-error" role="alert">
        {message?.tone === "error" ? message.text : ""}
      </p>
    </div>
  );
}
