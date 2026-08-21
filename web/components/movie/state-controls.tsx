"use client";

import { useState } from "react";

import { Icon } from "@/components/ui/icons";
import type { MovieState } from "@/lib/movie-types";
import "./state-controls.css";

export function StateControls({
  title,
  initialState,
  compact = false,
}: {
  title: string;
  initialState: MovieState;
  compact?: boolean;
}) {
  const [movieState, setMovieState] = useState(initialState);
  const [announcement, setAnnouncement] = useState("");

  function toggle(key: "watched" | "watchlisted") {
    const next = !movieState[key];
    setMovieState((current) => ({ ...current, [key]: next }));
    setAnnouncement(
      `${title} ${next ? "marked" : "unmarked"} as ${key === "watchlisted" ? "in watchlist" : "watched"}. Preview only.`,
    );
  }

  return (
    <div className={`state-controls ${compact ? "state-controls-compact" : ""}`}>
      <button
        aria-pressed={movieState.watchlisted}
        className={movieState.watchlisted ? "button-primary" : "button-secondary"}
        onClick={() => toggle("watchlisted")}
        type="button"
      >
        <Icon name="bookmark" />
        {movieState.watchlisted ? "In watchlist" : "Watchlist"}
      </button>
      <button
        aria-pressed={movieState.watched}
        className="button-secondary"
        onClick={() => toggle("watched")}
        type="button"
      >
        <Icon name="check" />
        {movieState.watched ? "Watched" : "Mark watched"}
      </button>
      <p aria-live="polite" className="visually-hidden">
        {announcement}
      </p>
    </div>
  );
}
