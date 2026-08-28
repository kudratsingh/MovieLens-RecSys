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
 *
 * The stars themselves are `RatingStars` rather than the compact
 * `MovieRatingControl` the passing surfaces use. Detail is the one place where
 * rating is the point rather than an incidental edit, so it gets the larger
 * control, the preview, the acknowledgement, and the collapse to a chip; every
 * other surface keeps the small editor. Both report the same intent to the same
 * write path, which is what keeps them from drifting apart in meaning.
 */

import {
  DETAIL_CONTROLS,
  MovieStateControls,
} from "@/components/movie/movie-state-controls";
import { RatingStars } from "@/components/movie/rating-stars";
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
  const { display, state, pending, message, run } = useMovieState({
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

      <RatingStars
        busy={busy}
        className="movie-state-panel-rating"
        clearLabel="Clear rating"
        idPrefix={idPrefix}
        // One sentence, both claims, in the order they matter: what pressing a
        // star records, and what the recorded number is not. ADR 0012 —
        // serving counts a rating as one observed watch and never reads its
        // magnitude, so a 1 and a 5 are the same learned signal today.
        note={
          display.watched
            ? "The star value is display feedback today, not a graded training signal."
            : "Rating this records a watch; the star value is display feedback today, not a graded training signal."
        }
        onRate={(value, control) => void run(ratingAction(value), control)}
        // The committed value, never the optimistic one: the acknowledgement
        // has to be the answer to a write that landed, not to a press. The
        // optimistic frame is handed over separately so the row still fills the
        // instant it is pressed — it just does not celebrate a write that could
        // still fail and roll back.
        pendingRating={pending === "rating" ? display.rating : null}
        rating={state?.rating ?? null}
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
