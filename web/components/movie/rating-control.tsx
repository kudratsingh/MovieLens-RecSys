"use client";

import { useId, useState } from "react";

import { Icon } from "@/components/ui/icons";
import "./rating-control.css";

/**
 * Stars, in the same two modes as `StateControls`: an uncontrolled preview, or
 * a controlled live control whose caller performs the mutation.
 *
 * `note` exists because the honest thing to say about a star is route-specific
 * and load-bearing. Under ADR 0002 and ADR 0012 the deployed recommender
 * treats one star and five stars as the same observed watch, so a route that
 * shows this control has to say what the number does and does not do.
 */
export function RatingControl({
  title,
  initialRating = null,
  rating,
  onRate,
  busy = false,
  note,
  idPrefix,
}: {
  title: string;
  initialRating?: number | null;
  /** Canonical rating from the caller. Supplying it makes the control live. */
  rating?: number | null;
  onRate?: (value: number, control: HTMLButtonElement) => void;
  busy?: boolean;
  note?: React.ReactNode;
  idPrefix?: string;
}) {
  const [previewRating, setPreviewRating] = useState(initialRating);
  const generatedPrefix = useId();
  const prefix = idPrefix ?? generatedPrefix;
  const live = typeof onRate === "function";
  const current = live ? (rating ?? null) : previewRating;

  return (
    <fieldset className="rating-control">
      <legend>Your rating</legend>
      <div className="rating-stars">
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            aria-label={`${value} ${value === 1 ? "star" : "stars"} for ${title}`}
            aria-pressed={current === value}
            className={current !== null && value <= current ? "rating-active" : ""}
            disabled={busy}
            id={`${prefix}-star-${value}`}
            key={value}
            onClick={(event) => {
              if (onRate) onRate(value, event.currentTarget);
              else setPreviewRating(value);
            }}
            type="button"
          >
            <Icon name="star" />
          </button>
        ))}
      </div>
      <p aria-live="polite">
        {live
          ? (current ? `${current} out of 5 recorded` : "Not rated")
          : current
            ? `${current} out of 5 — preview only`
            : "Not rated"}
      </p>
      {note ? <p className="rating-note">{note}</p> : null}
    </fieldset>
  );
}
