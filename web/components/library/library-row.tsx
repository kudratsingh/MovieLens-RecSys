"use client";

import Link from "next/link";

import {
  MovieRatingControl,
  MovieStateControls,
  type MovieStateControlSet,
} from "@/components/movie/movie-state-controls";
import type { LibraryMovie } from "@/lib/api";
import {
  leftCollectionNote,
  movieMatchesTab,
  stateSummary,
  titleInitials,
} from "@/lib/library/collection";
import type { LibraryTab } from "@/lib/library/url-state";
import {
  displayState,
  ratingAction,
  type MovieStateAction,
} from "@/lib/movie-state/actions";

export function libraryRowAnchorId(movieId: number): string {
  return `library-movie-${movieId}`;
}

/** The id prefix every control in a row shares, so focus can find them again. */
export function libraryRowControlPrefix(movieId: number): string {
  return `library-${movieId}`;
}

/**
 * Which controls a row offers, and in what order.
 *
 * The collections are deliberately not identical. Rated is for editing or
 * clearing a star value, Watchlist leads with `Mark watched` and lets a title
 * be released or excluded, and History owns the one destructive action in the
 * route. Showing every control everywhere was the older behaviour and it made
 * `Remove rating` and `Remove from history` look like the same button.
 */
export function libraryControlSet(
  tab: LibraryTab,
  watched: boolean,
): MovieStateControlSet {
  if (tab === "watchlist") {
    return [
      { kind: "watched", mode: "mark" },
      { kind: "watchlist", mode: "remove" },
      { kind: "dismissal", mode: "toggle" },
    ];
  }
  const dismissal: MovieStateControlSet = [{ kind: "dismissal", mode: "undo" }];
  // A History row that has lost its watched interaction offers nothing to
  // remove; it is on its way out of the collection.
  if (tab === "history" && watched) {
    return [{ kind: "watched", mode: "confirm" }, ...dismissal];
  }
  return dismissal;
}

/**
 * One movie in a collection, with the canonical controls for the collection it
 * is being shown in.
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
  onAction: (action: MovieStateAction, control: HTMLElement) => void;
}) {
  const state = movie.state;
  const anchorId = libraryRowAnchorId(movie.movie_id);
  const prefix = libraryRowControlPrefix(movie.movie_id);
  const inactive = !movieMatchesTab(state, tab);
  const locked = busy || disabled;
  const classNames = {
    root: "library-row-actions",
    action: "library-action",
    confirm: "library-confirm",
  };

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

      <MovieStateControls
        busy={locked}
        classNames={classNames}
        confirmation={{
          trigger: "Remove from history",
          action: "Remove from history",
          groupLabel: `Confirm removing ${movie.title} from watched history`,
          consequence: `Removing ${movie.title} from history deletes the watched interaction and its rating. It stops counting as a positive signal for ${persona}, and the title can appear again as unseen.`,
        }}
        controls={libraryControlSet(tab, state.watched_at !== null)}
        idPrefix={prefix}
        onAction={onAction}
        state={displayState(state)}
        title={movie.title}
      >
        {tab === "watchlist" ? null : (
          <MovieRatingControl
            busy={locked}
            classNames={{ root: "library-rating", action: "library-action" }}
            clearLabel="Remove rating"
            idPrefix={prefix}
            mode="half-star-select"
            onRate={(value, control) => onAction(ratingAction(value), control)}
            rating={state.rating}
            title={movie.title}
          />
        )}
      </MovieStateControls>
    </li>
  );
}
