"use client";

import { useId, useState } from "react";

import { Icon } from "@/components/ui/icons";
import type { MovieState } from "@/lib/movie-types";
import "./state-controls.css";

export type MovieStateAction = {
  resource: "watched" | "watchlist" | "dismissal";
  method: "PUT" | "DELETE";
};

/**
 * The one watched / watchlist / dismissal control surface.
 *
 * It has two modes so live routes and the recorded preview share a single
 * component instead of forking a look-alike. Without `onAction` it stays the
 * Bundle 4 preview toggle that announces `Preview only`. With `onAction` it
 * becomes controlled: the caller owns the canonical state, performs the
 * mutation, and reconciles the committed response, and this component only
 * reports intent. That split keeps mutation policy — idempotency, revision,
 * rollback, refresh — in one place per route rather than inside a button.
 */
export function StateControls({
  title,
  initialState,
  compact = false,
  state,
  onAction,
  busy = false,
  showDismiss = false,
  idPrefix,
}: {
  title: string;
  initialState: MovieState;
  compact?: boolean;
  /** Canonical state from the caller. Supplying it makes the control live. */
  state?: MovieState;
  onAction?: (action: MovieStateAction, control: HTMLButtonElement) => void;
  busy?: boolean;
  /** `Not for me` is a durable exclusion, so routes opt in deliberately. */
  showDismiss?: boolean;
  idPrefix?: string;
}) {
  const [previewState, setPreviewState] = useState(initialState);
  const [announcement, setAnnouncement] = useState("");
  const generatedPrefix = useId();
  const prefix = idPrefix ?? generatedPrefix;
  const live = typeof onAction === "function";
  const movieState = state ?? previewState;

  function act(
    action: MovieStateAction,
    control: HTMLButtonElement,
    key: "watched" | "watchlisted",
  ) {
    if (onAction) {
      onAction(action, control);
      return;
    }
    const next = !previewState[key];
    setPreviewState((current) => ({ ...current, [key]: next }));
    setAnnouncement(
      `${title} ${next ? "marked" : "unmarked"} as ${key === "watchlisted" ? "in watchlist" : "watched"}. Preview only.`,
    );
  }

  // Removing watched history also clears the rating, so it stays a confirmed
  // action in Library rather than a second click on a recommendation.
  const watchedIsFinal = live && movieState.watched;

  return (
    <div className={`state-controls ${compact ? "state-controls-compact" : ""}`}>
      <button
        aria-pressed={movieState.watchlisted}
        className={movieState.watchlisted ? "button-primary" : "button-secondary"}
        disabled={busy}
        id={`${prefix}-watchlist`}
        onClick={(event) =>
          act(
            {
              resource: "watchlist",
              method: movieState.watchlisted ? "DELETE" : "PUT",
            },
            event.currentTarget,
            "watchlisted",
          )
        }
        type="button"
      >
        <Icon name="bookmark" />
        {movieState.watchlisted ? "In watchlist" : "Watchlist"}
      </button>
      <button
        aria-pressed={movieState.watched}
        className="button-secondary"
        disabled={busy || watchedIsFinal}
        id={`${prefix}-watched`}
        onClick={(event) =>
          act(
            { resource: "watched", method: movieState.watched ? "DELETE" : "PUT" },
            event.currentTarget,
            "watched",
          )
        }
        type="button"
      >
        <Icon name="check" />
        {movieState.watched ? "Watched" : "Mark watched"}
      </button>
      {showDismiss && onAction ? (
        <button
          aria-pressed={movieState.suppressed}
          className="button-quiet"
          disabled={busy}
          id={`${prefix}-dismissal`}
          onClick={(event) =>
            onAction(
              {
                resource: "dismissal",
                method: movieState.suppressed ? "DELETE" : "PUT",
              },
              event.currentTarget,
            )
          }
          type="button"
        >
          {movieState.suppressed ? "Undo not for me" : "Not for me"}
        </button>
      ) : null}
      {live ? null : (
        // Live routes announce the committed result themselves; a second live
        // region here would either duplicate it or contradict it.
        <p aria-live="polite" className="visually-hidden">
          {announcement}
        </p>
      )}
    </div>
  );
}
