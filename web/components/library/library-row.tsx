"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

import {
  MovieRatingControl,
  MovieStateControls,
  type MovieStateControlSet,
  type RemovalConfirmation,
} from "@/components/movie/movie-state-controls";
import { PosterFallbackMark } from "@/components/movie/poster-card";
import type { LibraryMovie } from "@/lib/api";
import {
  leftCollectionNote,
  movieMatchesTab,
  movieMetaLine,
  stateSummary,
  tmdbMarkText,
} from "@/lib/library/collection";
import type { LibraryTab } from "@/lib/library/url-state";
import {
  displayState,
  ratingAction,
  type MovieStateAction,
} from "@/lib/movie-state/actions";
import { displayTitle } from "@/lib/movie-types";

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
 * What removing a watched title costs, said the same way wherever it is asked.
 *
 * The Seen spotlight and the row beneath it both own this action for the same
 * movie, and a consequence sentence that differed between them would be two
 * claims about one deletion. Shared for the same reason `libraryControlSet` is.
 */
export function libraryRemovalConfirmation(
  name: string,
  persona: string,
): RemovalConfirmation {
  return {
    trigger: "Remove from history",
    action: "Remove from history",
    groupLabel: `Confirm removing ${name} from watched history`,
    consequence: `Removing ${name} from history deletes the watched interaction and its rating. It stops counting as a positive signal for ${persona}, and the title can appear again as unseen.`,
  };
}

/**
 * The row's artwork slot.
 *
 * Library was the one route in a poster-first product with no artwork on any
 * tab at any width, because the payload used to carry no poster; it does now,
 * so the gap closes with the same treatment every other surface uses. The
 * whole slot is decorative — the row prints the title as a link right beside
 * it, and a second announcement of the same name (or of "Artwork unavailable"
 * twelve times down a list) is noise rather than information.
 *
 * The failed source is tracked rather than a bare boolean for the reason
 * `PosterCard` does the same: a row that re-renders in place for a different
 * movie must not inherit the previous one's broken poster.
 */
function LibraryThumb({ poster, title }: { poster: string | null; title: string }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  return (
    <span aria-hidden="true" className="library-thumb">
      {poster && failedSrc !== poster ? (
        <Image
          alt=""
          fill
          onError={() => setFailedSrc(poster)}
          sizes="72px"
          src={poster}
        />
      ) : (
        <PosterFallbackMark title={title} />
      )}
    </span>
  );
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
  // Coerced rather than trusted: the API and the web app deploy as separate
  // images, so a backend that predates these fields sends no key at all and
  // hands this row `undefined` behind a type that promises `number | null`.
  const year = movie.release_year ?? null;
  const poster = movie.poster_url ?? null;
  const tmdbMark = tmdbMarkText(movie.tmdb_rating);
  // The one name this row uses everywhere: the link, the poster mark, the
  // control labels, and the removal consequence. MovieLens titles carry their
  // year, and the row now prints that year on its own metadata line.
  const name = displayTitle(movie.title, year);
  const classNames = {
    root: "library-row-actions",
    action: "library-action",
    confirm: "library-confirm",
  };

  return (
    <li className={`library-row${inactive ? " library-row-inactive" : ""}`}>
      <LibraryThumb poster={poster} title={name} />

      <div className="library-row-main">
        <h3 className="library-row-title">
          <Link href={href} id={anchorId}>
            {name}
          </Link>
        </h3>
        <p className="library-row-genres">
          {movieMetaLine(year, movie.genres)}
          {tmdbMark ? <span className="library-row-tmdb">{tmdbMark}</span> : null}
        </p>
        <p className="library-row-state">{stateSummary(state, tab).join(" · ")}</p>
        {inactive ? (
          <p className="library-row-note">{leftCollectionNote(tab)}</p>
        ) : null}
      </div>

      <MovieStateControls
        busy={locked}
        classNames={classNames}
        confirmation={libraryRemovalConfirmation(name, persona)}
        controls={libraryControlSet(tab, state.watched_at !== null)}
        idPrefix={prefix}
        onAction={onAction}
        state={displayState(state)}
        title={name}
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
            title={name}
          />
        )}
      </MovieStateControls>
    </li>
  );
}
