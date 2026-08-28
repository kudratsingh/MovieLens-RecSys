"use client";

/**
 * Movie detail's state panel: the shared controls wired to the shared write
 * path, plus the two things a movie's own page owns.
 *
 * The first is hierarchy. Detail is where a title is managed, so `Watchlist`
 * leads while the movie is unseen and removing watched history is confirmed —
 * both expressed as a control set handed to the shared component, not as a
 * second implementation of it.
 *
 * The second is the rating panel, which is always reachable rather than gated
 * behind `Mark watched`. Setting a rating *is* recording a watched interaction
 * on the API side, so hiding the stars until watched would make the shorter
 * path to the same committed state unreachable — and would leave a viewer who
 * wants to rate something clicking two buttons to say one thing.
 */

import {
  DETAIL_CONTROLS,
  MovieRatingControl,
  MovieStateControls,
} from "@/components/movie/movie-state-controls";
import { useMovieState } from "@/components/movie/use-movie-state";
import type { MovieState } from "@/lib/api";
import { ratingAction } from "@/lib/movie-state/actions";
import type { MovieStateClient } from "@/lib/movie-state/client";
import "./movie-state-panel.css";

export function MovieStatePanel({
  userId,
  movieId,
  title,
  initialState,
  client,
  onCommitted,
}: {
  userId: number;
  movieId: number;
  title: string;
  initialState: MovieState | null;
  client?: MovieStateClient;
  onCommitted?: (state: MovieState) => void;
}) {
  const { display, pending, message, run } = useMovieState({
    userId,
    movieId,
    title,
    initialState,
    voice: "detail",
    client,
    onCommitted,
  });
  const busy = pending !== null;
  const idPrefix = `movie-state-${movieId}`;

  return (
    <div className="movie-state-panel">
      <MovieStateControls
        busy={busy}
        classNames={{ root: "movie-state-panel-primary" }}
        confirmation={{
          trigger: "Watched · remove",
          action: "Confirm removal",
          groupLabel: `Confirm removing ${title} from watched history`,
          consequence: `Removing ${title} from history also removes the watched interaction the recommender observed. Your rating goes with it.`,
        }}
        controls={DETAIL_CONTROLS}
        idPrefix={idPrefix}
        onAction={(action, control) => void run(action, control)}
        pending={pending}
        state={display}
        title={title}
      />

      <MovieRatingControl
        busy={busy}
        classNames={{ root: "movie-state-panel-rating" }}
        clearLabel="Clear rating"
        idPrefix={idPrefix}
        note={
          display.watched
            ? "Star magnitude is display feedback today, not a graded training signal."
            : "Rating this records it as watched history. Star magnitude is display feedback today, not a graded training signal."
        }
        onRate={(value, control) => void run(ratingAction(value), control)}
        rating={display.rating}
        // The panel already says what a star commits to; a second "N out of 5
        // recorded" line under it would restate the stars themselves.
        showRecorded={false}
        title={title}
      />

      {/*
        Two regions rather than one whose role flips: a live region has to be
        in the accessibility tree before its text changes, and swapping the
        role between renders is exactly how announcements get dropped.
      */}
      <p
        className={`movie-state-panel-message${message?.tone === "note" ? " is-note" : ""}`}
        role="status"
      >
        {/* A refused transition shares the polite region with a success: it is
            a rule being stated, not a failure to interrupt anyone with. */}
        {message && message.tone !== "error" ? message.text : ""}
      </p>
      <p className="movie-state-panel-message is-error" role="alert">
        {message?.tone === "error" ? message.text : ""}
      </p>
    </div>
  );
}
