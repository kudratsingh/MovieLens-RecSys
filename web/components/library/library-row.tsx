"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import type { LibraryMovie } from "@/lib/api";
import {
  leftCollectionNote,
  movieMatchesTab,
  stateSummary,
  titleInitials,
  type LibraryAction,
} from "@/lib/library/collection";
import type { LibraryTab } from "@/lib/library/url-state";

const RATING_VALUES = Array.from({ length: 10 }, (_, index) => (index + 1) / 2);

export function libraryRowAnchorId(movieId: number): string {
  return `library-movie-${movieId}`;
}

/**
 * One movie in a collection, with the canonical controls for the collection it
 * is being shown in.
 *
 * The controls are deliberately not identical across tabs. Rated is for editing
 * or clearing a star value, Watchlist is for opening or releasing a saved
 * title, and History owns the one destructive action in the route. Showing
 * every control everywhere was the older behaviour and it made
 * `Remove rating` and `Remove from history` look like the same button.
 */
export function LibraryRow({
  movie,
  tab,
  persona,
  href,
  busy,
  disabled,
  onAction,
}: {
  movie: LibraryMovie;
  tab: LibraryTab;
  persona: string;
  href: string;
  busy: boolean;
  disabled: boolean;
  onAction: (action: LibraryAction, focusId: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);

  const state = movie.state;
  const anchorId = libraryRowAnchorId(movie.movie_id);
  const ratingId = `library-rating-${movie.movie_id}`;
  const removeId = `library-remove-${movie.movie_id}`;
  const consequenceId = `library-remove-consequence-${movie.movie_id}`;
  const inactive = !movieMatchesTab(state, tab);
  const locked = busy || disabled;

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  function cancelConfirm() {
    setConfirming(false);
    // The trigger is unmounted while the confirmation is open, so focus has to
    // wait for the row to render it again.
    requestAnimationFrame(() => document.getElementById(removeId)?.focus());
  }

  const ratingControl = (
    <span className="library-rating">
      <label className="visually-hidden" htmlFor={ratingId}>
        Rating for {movie.title}
      </label>
      <select
        className="library-select"
        disabled={locked}
        id={ratingId}
        onChange={(event) => {
          const rating = Number(event.target.value);
          if (rating) onAction({ kind: "rate", rating }, ratingId);
        }}
        value={state.rating ?? ""}
      >
        <option disabled value="">
          Rate
        </option>
        {RATING_VALUES.map((rating) => (
          <option key={rating} value={rating}>
            {rating.toFixed(1)} stars
          </option>
        ))}
      </select>
    </span>
  );

  const clearRating =
    state.rating !== null ? (
      <button
        className="button-quiet library-action"
        disabled={locked}
        onClick={() => onAction({ kind: "clear-rating" }, ratingId)}
        type="button"
      >
        Remove rating
      </button>
    ) : null;

  return (
    <li className={`library-row${inactive ? " library-row-inactive" : ""}`}>
      <span aria-hidden="true" className="library-thumb">
        {titleInitials(movie.title)}
      </span>

      <div className="library-row-main">
        <h3 className="library-row-title">
          <Link href={href} id={anchorId}>
            {movie.title}
          </Link>
        </h3>
        <p className="library-row-genres">
          {movie.genres.length ? movie.genres.join(" · ") : "Genres unavailable"}
        </p>
        <p className="library-row-state">{stateSummary(state, tab).join(" · ")}</p>
        {inactive ? (
          <p className="library-row-note">{leftCollectionNote(tab)}</p>
        ) : null}
      </div>

      {confirming ? (
        <div
          aria-label={`Confirm removing ${movie.title} from watched history`}
          className="library-confirm"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              cancelConfirm();
            }
          }}
          role="group"
        >
          <p id={consequenceId}>
            Removing {movie.title} from history deletes the watched interaction and
            its rating. It stops counting as a positive signal for {persona}, and
            the title can appear again as unseen.
          </p>
          <span className="library-confirm-actions">
            <button
              aria-describedby={consequenceId}
              className="library-destructive"
              disabled={locked}
              onClick={() => {
                setConfirming(false);
                onAction({ kind: "remove-history" }, anchorId);
              }}
              ref={confirmRef}
              type="button"
            >
              Remove from history
            </button>
            <button
              className="button-quiet library-action"
              disabled={locked}
              onClick={cancelConfirm}
              type="button"
            >
              Keep it
            </button>
          </span>
        </div>
      ) : (
        <div className="library-row-actions">
          {tab === "rated" ? (
            <>
              {ratingControl}
              {clearRating}
            </>
          ) : null}

          {tab === "history" ? (
            <>
              {ratingControl}
              {clearRating}
              {state.watched_at ? (
                <button
                  className="button-quiet library-action library-action-danger"
                  disabled={locked}
                  id={removeId}
                  onClick={() => setConfirming(true)}
                  type="button"
                >
                  Remove from history
                </button>
              ) : null}
            </>
          ) : null}

          {tab === "watchlist" ? (
            <>
              <button
                className="button-secondary library-action"
                disabled={locked}
                id={removeId}
                onClick={() => onAction({ kind: "mark-watched" }, removeId)}
                type="button"
              >
                Mark watched
              </button>
              {state.watchlisted_at ? (
                <button
                  className="button-quiet library-action"
                  disabled={locked}
                  onClick={() => onAction({ kind: "unsave" }, anchorId)}
                  type="button"
                >
                  Remove from watchlist
                </button>
              ) : null}
              {!state.dismissed_at ? (
                <button
                  className="button-quiet library-action"
                  disabled={locked}
                  onClick={() => onAction({ kind: "dismiss" }, anchorId)}
                  type="button"
                >
                  Not for me
                </button>
              ) : null}
            </>
          ) : null}

          {state.dismissed_at ? (
            <button
              className="button-quiet library-action"
              disabled={locked}
              onClick={() => onAction({ kind: "undismiss" }, anchorId)}
              type="button"
            >
              Undo not for me
            </button>
          ) : null}
        </div>
      )}
    </li>
  );
}
