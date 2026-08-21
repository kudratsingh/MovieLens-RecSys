"use client";

import { useState } from "react";

import { Icon } from "@/components/ui/icons";
import "./rating-control.css";

export function RatingControl({ title, initialRating = null }: { title: string; initialRating?: number | null }) {
  const [rating, setRating] = useState(initialRating);

  return (
    <fieldset className="rating-control">
      <legend>Your rating</legend>
      <div className="rating-stars">
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            aria-label={`${value} ${value === 1 ? "star" : "stars"} for ${title}`}
            aria-pressed={rating === value}
            className={rating !== null && value <= rating ? "rating-active" : ""}
            key={value}
            onClick={() => setRating(value)}
            type="button"
          >
            <Icon name="star" />
          </button>
        ))}
      </div>
      <p aria-live="polite">{rating ? `${rating} out of 5 — preview only` : "Not rated"}</p>
    </fieldset>
  );
}
