"use client";

/**
 * The star rating on a movie's own page: a bigger, brighter control that
 * acknowledges the press and then gets out of the way.
 *
 * Three things separate it from the compact editor in `movie-state-controls`,
 * and each of them is about detail being the surface where a rating is a
 * decision rather than an incidental edit:
 *
 * 1. **It previews.** Hover and keyboard focus fill the row from the left, so
 *    the value is visible before it is committed. The compact editor has no
 *    room for that and does not try.
 * 2. **It acknowledges.** A commit fills the stars in a short stagger, pops the
 *    chosen one once, and fades a glow behind it. This is the one overshoot in
 *    the product: a rating is the only control here whose whole job is to
 *    record what the viewer thinks, and a press that produced no visible answer
 *    was the thing the panel was actually missing.
 * 3. **It collapses.** Once a rating exists, five large stars are five controls
 *    for a decision already made, so the row folds into a chip that states the
 *    value and offers one way back in. That is also why the page does not open
 *    with the row expanded for an already-rated movie: the stars are there to
 *    *set* a rating, and the chip is there to say one is set.
 *
 * The component never writes. It reports the value the viewer chose and renders
 * what the caller tells it is committed, exactly like the rest of the movie-
 * state family — mutation policy stays in `lib/movie-state/`.
 *
 * The commit sequence is driven by timers rather than by `animationend` so it
 * behaves identically under fake timers, under `prefers-reduced-motion` (where
 * it is skipped outright), and in a browser that never fires the event because
 * the element was scrolled out of a compositor's way.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Icon } from "@/components/ui/icons";
import { ratingValueText } from "@/lib/movie-details";
import "./rating-stars.css";

/** Whole stars only. Half-star precision belongs to the Library's editor. */
export const STAR_VALUES = [1, 2, 3, 4, 5] as const;

/** Gap between two stars filling. Mirrors `--motion-stagger`. */
export const STAGGER_MS = 40;
/** The single scale pop on the chosen star. Mirrors `--motion-pop`. */
export const POP_MS = 280;
/** The row folding into the chip. Mirrors `--motion-collapse`. */
export const COLLAPSE_MS = 200;
/** Fill sweep plus pop: the whole acknowledgement, before the fold begins. */
export const CELEBRATION_MS = STAGGER_MS * (STAR_VALUES.length - 1) + POP_MS;

type Phase = "stars" | "celebrating" | "collapsing" | "chip";

function prefersReducedMotion(): boolean {
  // Read at commit time rather than at render: a media query read during render
  // makes the server and the first client pass disagree, and the setting can
  // change between two ratings in the same session.
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function RatingStars({
  title,
  rating,
  pendingRating = null,
  onRate,
  busy = false,
  legend = "Your rating",
  clearLabel,
  note,
  idPrefix,
  className,
}: {
  title: string;
  /** The committed value, or null. Never the optimistic one. */
  rating: number | null;
  /**
   * The value currently being written, if any. It fills the row so a press has
   * an immediate answer, but it never triggers the acknowledgement: celebrating
   * a write that can still fail and roll back is exactly the lie the
   * commit-before-acknowledge rule exists to prevent.
   */
  pendingRating?: number | null;
  /** Reports the chosen value; `null` means "clear this rating". */
  onRate: (value: number | null, control: HTMLElement) => void;
  busy?: boolean;
  legend?: string;
  /** Rendered in the open state only, and only when there is a value to clear. */
  clearLabel?: string;
  /** The one sentence about what a star does and does not commit to. */
  note?: React.ReactNode;
  idPrefix?: string;
  className?: string;
}) {
  const generatedPrefix = useId();
  const prefix = idPrefix ?? generatedPrefix;

  const [phase, setPhase] = useState<Phase>(rating === null ? "stars" : "chip");
  const [preview, setPreview] = useState<number | null>(null);
  // Which star owns the group's single tab stop.
  const [roving, setRoving] = useState<number>(nearestStar(rating));
  const [announcement, setAnnouncement] = useState("");

  const rowRef = useRef<HTMLDivElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  // Seeded with the value we were handed, so a page that renders an already
  // rated movie shows the chip rather than replaying a celebration for a
  // rating committed days ago.
  const lastCommitted = useRef<number | null>(rating);
  // Set when the collapse begins with focus inside the row: the star that was
  // pressed is about to unmount, and dropping a keyboard viewer on `<body>` is
  // the one outcome this sequence must not produce.
  const returnFocus = useRef(false);
  const pendingFocus = useRef<"chip" | "stars" | null>(null);
  const timers = useRef<number[]>([]);

  const clearTimers = useCallback(() => {
    for (const timer of timers.current) window.clearTimeout(timer);
    timers.current = [];
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  /**
   * Acknowledge a value that is now stored, whether the API changed a row or
   * replied `no_change`.
   *
   * Re-confirming the value already on the record is the case that made this a
   * function rather than a branch inside the effect below: nothing about
   * `rating` changes, so an effect watching it would never fire and the row
   * would sit open after a press that was perfectly successful.
   */
  const acknowledge = useCallback(
    (value: number) => {
      clearTimers();
      setRoving(nearestStar(value));
      setAnnouncement(
        `Rated ${ratingValueText(value)} out of 5 for ${title}. Use Change rating to edit it.`,
      );
      returnFocus.current = Boolean(
        rowRef.current && rowRef.current.contains(document.activeElement),
      );

      if (prefersReducedMotion()) {
        setPhase("chip");
        return;
      }

      setPhase("celebrating");
      timers.current.push(
        window.setTimeout(() => setPhase("collapsing"), CELEBRATION_MS),
        window.setTimeout(() => setPhase("chip"), CELEBRATION_MS + COLLAPSE_MS),
      );
    },
    [clearTimers, title],
  );

  useEffect(() => {
    if (rating === lastCommitted.current) return;
    const previous = lastCommitted.current;
    lastCommitted.current = rating;

    if (rating === null) {
      // A cleared rating reopens the row: there is nothing left to summarise,
      // and the viewer is most likely about to choose a different value.
      //
      // The rule's usual advice — derive this during render instead — does not
      // apply. What changed is not a prop this component can read a conclusion
      // from: it is a write landing at the API, and the response to it is a
      // timed sequence that cannot be started from a render. The cascading
      // render the rule warns about is one frame, on a control the viewer has
      // just pressed.
      clearTimers();
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPhase("stars");
      setRoving(nearestStar(previous));
      setAnnouncement(`Rating removed for ${title}.`);
      return;
    }
    acknowledge(rating);
  }, [acknowledge, clearTimers, rating, title]);

  // Focus lands after the target exists, which is a render later than the
  // decision to move it.
  useEffect(() => {
    if (phase === "chip" && returnFocus.current) {
      returnFocus.current = false;
      chipRef.current?.focus();
    }
    if (pendingFocus.current === "stars" && phase === "stars") {
      pendingFocus.current = null;
      focusStar(rowRef.current, prefix, nearestStar(rating));
    }
  }, [phase, prefix, rating]);

  const reopen = useCallback(() => {
    pendingFocus.current = "stars";
    setPreview(null);
    setPhase("stars");
  }, []);

  if (phase === "chip") {
    return (
      <fieldset className={classes("rating-stars", className)}>
        <legend>{legend}</legend>
        <div className="rating-chip">
          <span aria-hidden="true" className="rating-chip-stars">
            {STAR_VALUES.map((value) => (
              <Icon
                className={value <= (rating ?? 0) ? "is-filled" : undefined}
                height={18}
                key={value}
                name="star"
                width={18}
              />
            ))}
          </span>
          <span className="rating-chip-value">
            You rated {ratingValueText(rating ?? 0)}/5
          </span>
          <span aria-hidden="true" className="rating-chip-separator">
            ·
          </span>
          <button
            aria-disabled={busy}
            aria-label={`Change rating for ${title}`}
            className="rating-chip-change"
            id={`${prefix}-change-rating`}
            onClick={() => {
              if (!busy) reopen();
            }}
            ref={chipRef}
            type="button"
          >
            Change rating
          </button>
        </div>
        {note ? <p className="rating-note">{note}</p> : null}
        <Announcement text={announcement} />
      </fieldset>
    );
  }

  const committed = phase === "celebrating" || phase === "collapsing" ? rating : null;
  const filledTo = preview ?? pendingRating ?? rating ?? 0;

  function move(next: number) {
    const clamped = Math.min(STAR_VALUES.length, Math.max(1, next));
    setRoving(clamped);
    setPreview(clamped);
    focusStar(rowRef.current, prefix, clamped);
  }

  return (
    <fieldset className={classes("rating-stars", className)}>
      <legend>{legend}</legend>
      <div className="rating-stars-controls">
        {/*
          One tab stop for five buttons, moved with the arrow keys. Tabbing
          through every star to reach `Clear rating` was five stops for one
          decision, and it is the pattern every rating widget a viewer has met
          already uses.
        */}
        <div
          className={classes(
            "rating-star-row",
            phase === "celebrating" && "is-celebrating",
            phase === "collapsing" && "is-collapsing",
          )}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setPreview(null);
          }}
          onKeyDown={(event) => {
            const key = event.key;
            if (key === "ArrowRight" || key === "ArrowUp") {
              event.preventDefault();
              move(roving + 1);
            } else if (key === "ArrowLeft" || key === "ArrowDown") {
              event.preventDefault();
              move(roving - 1);
            } else if (key === "Home") {
              event.preventDefault();
              move(1);
            } else if (key === "End") {
              event.preventDefault();
              move(STAR_VALUES.length);
            }
          }}
          onMouseLeave={() => setPreview(null)}
          ref={rowRef}
        >
          {STAR_VALUES.map((value) => (
            <button
              aria-disabled={busy}
              aria-pressed={rating === value}
              className={classes(
                "rating-star",
                value <= filledTo && "is-filled",
                committed !== null && value <= committed && "is-committing",
                committed === value && "is-chosen",
              )}
              id={starId(prefix, value)}
              key={value}
              onClick={(event) => {
                if (busy) return;
                setPreview(null);
                onRate(value, event.currentTarget);
                // Re-confirming the stored value produces no state change for
                // the effect above to notice, so the acknowledgement is run
                // here instead. Everything else waits for the commit.
                if (rating === value) acknowledge(value);
              }}
              onFocus={() => {
                setRoving(value);
                setPreview(value);
              }}
              onMouseEnter={() => setPreview(value)}
              style={{ "--star-index": value - 1 } as React.CSSProperties}
              tabIndex={value === roving ? 0 : -1}
              type="button"
            >
              <Icon className="rating-star-glyph" name="star" />
              <span className="visually-hidden">
                {value} {value === 1 ? "star" : "stars"} for {title}
              </span>
            </button>
          ))}
        </div>

        {/* Not during the acknowledgement: a `Clear rating` that appears for
            640ms and leaves with the row is a control nobody could use and an
            offer to undo a decision the viewer has only just made. */}
        {clearLabel && rating !== null && phase === "stars" ? (
          <button
            aria-disabled={busy}
            className="button-quiet rating-clear"
            id={`${prefix}-clear-rating`}
            onClick={(event) => {
              if (busy) return;
              // The row survives the clear; the button that asked for it does
              // not, so it is the wrong thing to hand back for focus recovery.
              onRate(null, rowRef.current ?? event.currentTarget);
            }}
            type="button"
          >
            {clearLabel}
          </button>
        ) : null}
      </div>
      {note ? <p className="rating-note">{note}</p> : null}
      <Announcement text={announcement} />
    </fieldset>
  );
}

/**
 * Polite, and deliberately not `role="status"`: the panel around this already
 * owns a status region carrying what the API committed, and two status roles in
 * one panel is how one of them stops being read at all. This one says the thing
 * that region cannot — that the control itself changed shape.
 */
function Announcement({ text }: { text: string }) {
  return (
    <span aria-live="polite" className="visually-hidden">
      {text}
    </span>
  );
}

export function starId(prefix: string, value: number): string {
  return `${prefix}-star-${value}`;
}

function focusStar(row: HTMLElement | null, prefix: string, value: number) {
  const star = row?.ownerDocument.getElementById(starId(prefix, value));
  if (star instanceof HTMLElement) star.focus();
}

/** Half-star values arrive from the Library; the row is whole stars. */
function nearestStar(rating: number | null): number {
  if (rating === null) return 1;
  return Math.min(STAR_VALUES.length, Math.max(1, Math.round(rating)));
}

function classes(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}
