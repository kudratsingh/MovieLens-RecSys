"use client";

/**
 * The one watched / rating / watchlist / dismissal control surface.
 *
 * Discover cards, the featured movie, movie detail, and Library rows all show
 * the same four states, and until Bundle 7 they showed them through three
 * separate implementations that had already drifted apart in copy, in disabled
 * semantics, and in what a confirmation looked like. There is one component
 * now, and the differences between surfaces are expressed as a declared control
 * set rather than as a fork.
 *
 * The set is an ordered list on purpose: which controls a surface offers *and*
 * in what order is the documented hierarchy, so it belongs in the caller's
 * declaration rather than in this component's render order.
 *
 * - Discover ranks unseen movies, so `Watched` is a one-way action there: the
 *   destructive removal does not belong beside a recommendation.
 * - Movie detail is where a title is managed, so `Watchlist` leads while the
 *   movie is unseen and removing watched history is confirmed.
 * - A Library row shows the collection it is listed under, so Watchlist rows
 *   lead with `Mark watched` and History rows own the confirmed
 *   `Remove from history`.
 *
 * The component never performs a write. The caller owns the canonical state,
 * runs the mutation, and reconciles the committed response; this reports intent
 * and renders what it is told. That keeps mutation policy — idempotency,
 * revision, rollback, refresh — in the shared write path rather than inside a
 * button. The one exception is preview mode: without `onAction` the controls
 * toggle locally and say `Preview only`, which is what the recorded
 * `/ui-preview` surfaces render.
 *
 * In-flight controls are `aria-disabled`, not `disabled`: a disabled element
 * cannot hold focus, and returning focus to the control that failed is exactly
 * what a rollback has to do.
 */

import { Fragment, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/ui/icons";
import {
  toggleAction,
  UNKNOWN_MOVIE_STATE,
  type MovieDisplayState,
  type MovieStateAction,
  type MovieStateResource,
} from "@/lib/movie-state/actions";
import "./movie-state-controls.css";

export type WatchlistMode = "toggle" | "remove";
export type WatchedMode = "toggle" | "final" | "mark" | "confirm";
export type DismissalMode = "toggle" | "undo";

export type MovieStateControl =
  | { kind: "watchlist"; mode: WatchlistMode }
  | { kind: "watched"; mode: WatchedMode }
  | { kind: "dismissal"; mode: DismissalMode };

/** Which affordances a surface offers, in the order it offers them. */
export type MovieStateControlSet = readonly MovieStateControl[];

/** Copy for the one action that destroys a signal rather than adding one. */
export type RemovalConfirmation = {
  /** Opens the confirmation. */
  trigger: string;
  /** Commits the removal. */
  action: string;
  /** Names the confirmation for assistive technology. */
  groupLabel: string;
  consequence: React.ReactNode;
};

/**
 * Layout hooks owned by the surface. The family renders the markup and the
 * copy; where a control row sits in a Library grid or a detail column is not
 * its business.
 */
export type MovieStateClassNames = {
  root?: string;
  action?: string;
  confirm?: string;
};

export const RECOMMENDATION_CONTROLS: MovieStateControlSet = [
  { kind: "watchlist", mode: "toggle" },
  { kind: "watched", mode: "final" },
  { kind: "dismissal", mode: "toggle" },
];

export const DETAIL_CONTROLS: MovieStateControlSet = [
  { kind: "watchlist", mode: "toggle" },
  { kind: "watched", mode: "confirm" },
  { kind: "dismissal", mode: "toggle" },
];

export const PREVIEW_CONTROLS: MovieStateControlSet = [
  { kind: "watchlist", mode: "toggle" },
  { kind: "watched", mode: "toggle" },
];

type ControlButtonProps = {
  "aria-busy": boolean;
  "aria-disabled": boolean;
  id: string;
  type: "button";
};

function classes(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

export function MovieStateControls({
  title,
  state,
  initialState = UNKNOWN_MOVIE_STATE,
  controls,
  confirmation,
  onAction,
  busy = false,
  pending = null,
  compact = false,
  idPrefix,
  classNames,
  children,
}: {
  title: string;
  /** Canonical (or optimistic) state from the caller. Ignored in preview mode. */
  state?: MovieDisplayState;
  /** Preview-mode seed, used only when `onAction` is absent. */
  initialState?: MovieDisplayState;
  controls: MovieStateControlSet;
  /** Required by a `watched: "confirm"` control. */
  confirmation?: RemovalConfirmation;
  onAction?: (action: MovieStateAction, control: HTMLButtonElement) => void;
  busy?: boolean;
  pending?: MovieStateResource | null;
  compact?: boolean;
  idPrefix?: string;
  classNames?: MovieStateClassNames;
  /**
   * Controls the surface adds to the same row — the Library's rating editor is
   * the only one today. They lead the row and, like the buttons, are replaced
   * while a removal is being confirmed: a confirmation that left the rest of
   * the row live would invite a second decision on top of a destructive one.
   */
  children?: React.ReactNode;
}) {
  const [previewState, setPreviewState] = useState(initialState);
  const [announcement, setAnnouncement] = useState("");
  const [confirming, setConfirming] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const generatedPrefix = useId();
  const prefix = idPrefix ?? generatedPrefix;
  const live = typeof onAction === "function";
  const current = live ? (state ?? UNKNOWN_MOVIE_STATE) : previewState;
  const consequenceId = `${prefix}-consequence`;

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  function act(action: MovieStateAction) {
    return (event: React.MouseEvent<HTMLButtonElement>) => {
      if (busy) return;
      // Held as a DOM reference on purpose: React clears `currentTarget` on the
      // synthetic event once the handler returns, and focus recovery happens
      // after the request resolves.
      const control = event.currentTarget;
      setConfirming(false);
      if (onAction) {
        onAction(action, control);
        return;
      }
      setPreviewState((value) => nextPreview(value, action));
      setAnnouncement(previewAnnouncement(title, action));
    };
  }

  function cancelConfirm() {
    setConfirming(false);
    // The trigger is unmounted while the confirmation is open, so focus has to
    // wait for the row to render it again.
    requestAnimationFrame(() =>
      document.getElementById(`${prefix}-watched`)?.focus(),
    );
  }

  const buttonProps = (resource: MovieStateResource): ControlButtonProps => ({
    "aria-busy": pending === resource,
    "aria-disabled": busy,
    id: `${prefix}-${resource}`,
    type: "button",
  });

  if (confirming && confirmation) {
    return (
      <div
        aria-label={confirmation.groupLabel}
        className={classes("movie-state-confirm", classNames?.confirm)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            cancelConfirm();
          }
        }}
        role="group"
      >
        <p className="movie-state-consequence" id={consequenceId} role="status">
          {confirmation.consequence}
        </p>
        <span className="movie-state-confirm-actions">
          <button
            aria-describedby={consequenceId}
            aria-disabled={busy}
            className="movie-state-destructive"
            onClick={act({ resource: "watched", method: "DELETE" })}
            ref={confirmRef}
            type="button"
          >
            {confirmation.action}
          </button>
          <button
            className={classes("button-quiet", classNames?.action)}
            onClick={cancelConfirm}
            type="button"
          >
            Keep it
          </button>
        </span>
      </div>
    );
  }

  function renderControl(control: MovieStateControl): React.ReactNode {
    const props = buttonProps(control.kind);
    const action = classNames?.action;
    const kind = `movie-state-${control.kind}`;

    if (control.kind === "watchlist") {
      if (control.mode === "remove") {
        return current.watchlisted ? (
          <button
            {...props}
            className={classes("button-quiet", kind, action)}
            onClick={act({ resource: "watchlist", method: "DELETE" })}
          >
            Remove from watchlist
          </button>
        ) : null;
      }
      const saved = current.watchlisted;
      return (
        <button
          {...props}
          // At rail density a pill is one line wide, and `In watchlist` is two
          // of them. The compact rendering keeps the shorter word on screen and
          // moves the full state into the accessible name, so the pressed pill
          // is still `In watchlist` to a screen reader, to speech input, and to
          // every test that asks for it by name — while the visible label, and
          // therefore the pill's width, never changes as the state does.
          aria-label={compact && saved ? "In watchlist" : undefined}
          aria-pressed={saved}
          className={classes(
            saved ? "button-primary" : "button-secondary",
            kind,
            saved && "movie-state-on",
            action,
          )}
          onClick={act(toggleAction("watchlist", current))}
        >
          <Icon name="bookmark" />
          {compact || !saved ? "Watchlist" : "In watchlist"}
        </button>
      );
    }

    if (control.kind === "dismissal") {
      if (control.mode === "undo" && !current.dismissed) return null;
      return (
        <button
          {...props}
          aria-pressed={control.mode === "toggle" ? current.dismissed : undefined}
          className={classes(
            "button-quiet",
            kind,
            current.dismissed && "movie-state-on",
            action,
          )}
          onClick={act(toggleAction("dismissal", current))}
        >
          {current.dismissed ? "Undo not for me" : "Not for me"}
        </button>
      );
    }

    const watched = classes("button-secondary", kind, action);

    // The removal is confirmed rather than immediate, because it deletes the
    // one interaction the recommender actually observed.
    if (control.mode === "confirm" && current.watched && confirmation) {
      return (
        <button
          {...props}
          aria-pressed
          className={classes(watched, "movie-state-on", "movie-state-action-danger")}
          onClick={() => {
            if (!busy) setConfirming(true);
          }}
        >
          <Icon name="check" />
          {confirmation.trigger}
        </button>
      );
    }

    // Beside a recommendation, `Watched` is a statement rather than a toggle:
    // undoing it is a destructive edit and belongs in Library or on detail.
    if (control.mode === "final" && current.watched) {
      return (
        <button
          {...props}
          aria-disabled
          aria-pressed
          // `aria-disabled` here is permanent rather than in-flight, so it must
          // not borrow the faded, cursor-progress look that means "wait".
          className={classes(watched, "movie-state-on", "movie-state-recorded")}
        >
          <Icon name="check" />
          Watched
        </button>
      );
    }

    if (control.mode === "toggle" && current.watched) {
      return (
        <button
          {...props}
          aria-pressed
          className={classes(watched, "movie-state-on")}
          onClick={act({ resource: "watched", method: "DELETE" })}
        >
          <Icon name="check" />
          Watched
        </button>
      );
    }

    return (
      <button
        {...props}
        // Same rule as `Watchlist` above: the visible word shortens at rail
        // density, the action it performs does not. `Watched` is a substring of
        // `Mark watched`, so the accessible name still contains what is on
        // screen and speech input still reaches the control by what it reads.
        aria-label={compact ? "Mark watched" : undefined}
        aria-pressed={false}
        className={watched}
        onClick={act({ resource: "watched", method: "PUT" })}
      >
        <Icon name="check" />
        {compact ? "Watched" : "Mark watched"}
      </button>
    );
  }

  return (
    <div
      className={classes(
        "movie-state-row",
        compact && "movie-state-row-compact",
        classNames?.root,
      )}
    >
      {children}
      {controls.map((control) => (
        <Fragment key={control.kind}>{renderControl(control)}</Fragment>
      ))}

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

const STARS = [1, 2, 3, 4, 5];
/** The database constraint is half-star, which only the Library editor offers. */
const HALF_STARS = Array.from({ length: 10 }, (_, index) => (index + 1) / 2);

export function ratingInputId(idPrefix: string): string {
  return `${idPrefix}-rating`;
}

/**
 * The compact rating editor, in the two input shapes the product actually uses.
 *
 * Quick Picks offers whole stars, where one press is a queue decision that
 * marks watched and rates in a single write; the Library edits an existing
 * value and offers the half-star precision the stored constraint allows. The
 * surfaces where rating is the whole point of the moment — a movie's own page,
 * the Seen spotlight, Discover's `Just marked watched` prompt — use the larger
 * `RatingStars` instead.
 *
 * All of them write the same resource and all of them say the same thing about
 * what a star means, which is the part that has to stay in one place: under ADR
 * 0002 the deployed recommender counts any rating as one observed watch, so a 1
 * and a 5 are the same learned signal today.
 */
export function MovieRatingControl({
  title,
  rating,
  onRate,
  mode = "stars",
  busy = false,
  legend = "Your rating",
  clearLabel,
  note,
  idPrefix,
  classNames,
}: {
  title: string;
  rating: number | null;
  /** Reports the value the viewer chose; `null` means "clear this rating". */
  onRate: (value: number | null, control: HTMLElement) => void;
  mode?: "stars" | "half-star-select";
  busy?: boolean;
  legend?: string;
  /** Rendered only when a value exists to clear. */
  clearLabel?: string;
  note?: React.ReactNode;
  idPrefix?: string;
  classNames?: MovieStateClassNames;
}) {
  const generatedPrefix = useId();
  const prefix = idPrefix ?? generatedPrefix;
  const selectId = ratingInputId(prefix);
  // The editor itself, which survives a clear. The button that clears a rating
  // unmounts with the value it removed, so it is the wrong thing to hand back
  // to a caller doing focus recovery.
  const editorRef = useRef<HTMLElement>(null);

  const clear =
    clearLabel && rating !== null ? (
      <button
        aria-disabled={busy}
        className={classes("button-quiet", classNames?.action)}
        id={`${prefix}-clear-rating`}
        onClick={(event) => {
          if (busy) return;
          onRate(null, editorRef.current ?? event.currentTarget);
        }}
        type="button"
      >
        {clearLabel}
      </button>
    ) : null;

  if (mode === "half-star-select") {
    return (
      <>
        <span className={classes("movie-rating-select", classNames?.root)}>
          <label className="visually-hidden" htmlFor={selectId}>
            Rating for {title}
          </label>
          <select
            className="library-select"
            disabled={busy}
            id={selectId}
            ref={editorRef as React.RefObject<HTMLSelectElement>}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (value) onRate(value, event.target);
            }}
            value={rating ?? ""}
          >
            <option disabled value="">
              Rate
            </option>
            {HALF_STARS.map((value) => (
              <option key={value} value={value}>
                {value.toFixed(1)} stars
              </option>
            ))}
          </select>
        </span>
        {clear}
      </>
    );
  }

  return (
    <fieldset className={classes("movie-rating", classNames?.root)}>
      <legend>{legend}</legend>
      <div className="movie-rating-stars">
        {STARS.map((value) => (
          <button
            aria-disabled={busy}
            aria-label={`${value} ${value === 1 ? "star" : "stars"} for ${title}`}
            aria-pressed={rating === value}
            className={rating !== null && value <= rating ? "rating-active" : undefined}
            id={`${prefix}-star-${value}`}
            key={value}
            ref={value === 1 ? (editorRef as React.RefObject<HTMLButtonElement>) : undefined}
            onClick={(event) => {
              if (busy) return;
              onRate(value, event.currentTarget);
            }}
            type="button"
          >
            <Icon name="star" />
          </button>
        ))}
        {clear}
      </div>
      {/* The compact editor has no acknowledgement of its own, so the recorded
          value is the only thing that says a press landed. */}
      <p aria-live="polite" className="movie-rating-status">
        {rating ? `${rating} out of 5 recorded` : "Not rated"}
      </p>
      {note ? <p className="movie-rating-note">{note}</p> : null}
    </fieldset>
  );
}

function nextPreview(
  current: MovieDisplayState,
  action: MovieStateAction,
): MovieDisplayState {
  if (action.resource === "watchlist") {
    return { ...current, watchlisted: action.method === "PUT" };
  }
  if (action.resource === "watched") {
    return { ...current, watched: action.method === "PUT" };
  }
  return current;
}

function previewAnnouncement(title: string, action: MovieStateAction): string {
  const label = action.resource === "watchlist" ? "in watchlist" : "watched";
  const verb = action.method === "PUT" ? "marked" : "unmarked";
  return `${title} ${verb} as ${label}. Preview only.`;
}
