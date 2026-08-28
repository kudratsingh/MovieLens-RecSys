"use client";

/**
 * The Browse poster grid.
 *
 * Two properties are load-bearing here and both are about the grid not moving
 * under the viewer. Every cell reserves its poster at a fixed 2:3 ratio, so a
 * poster that 404s or times out swaps to the deterministic fallback inside the
 * space already allocated — nothing below it shifts. And a card's controls
 * reconcile state in place: saving or marking a title watched changes what the
 * card says, never its position in the order the endpoint returned.
 *
 * The controls themselves are the shared movie-state family, declared as a
 * control set below. A grid cell is not a place to reimplement what watched
 * means.
 *
 * Posters below the fold stay lazy (Next's default) and only the first cells
 * are marked priority, because the grid is the largest contentful paint on
 * this route and everything after the first row is a scroll away.
 */

import { useCallback, useRef, useState } from "react";

import {
  MovieStateControls,
  type MovieStateControlSet,
} from "@/components/movie/movie-state-controls";
import { PosterCard } from "@/components/movie/poster-card";
import { useMovieState } from "@/components/movie/use-movie-state";
import type { CatalogItem, MovieState } from "@/lib/api";
import {
  catalogItemToCard,
  metadataNote,
  stateNote,
} from "@/lib/browse/catalog-card";
import type { MovieStateAction } from "@/lib/movie-state/actions";
import { movieStateAnnouncement } from "@/lib/movie-state/announce";
import { displayTitle } from "@/lib/movie-types";
import "./catalog-grid.css";

/** Only the first row is eager; the rest of the grid stays lazy. */
const PRIORITY_CELLS = 2;

/**
 * What a browse card offers, in the order it offers it.
 *
 * The design contract asks for watched "through a visible secondary action",
 * and the card had only `Watchlist` — so the one thing a viewer can record from
 * a catalog grid was the one thing that changes no recommendation. Saving leads
 * because it is the reversible, organizational choice; `Watched` follows.
 *
 * `Watched` is `final` rather than `mark` for the reason it is on a
 * recommendation card: undoing a watched interaction deletes the only signal
 * the recommender observed, and that destructive edit belongs on movie detail
 * or in the Library, not on a 40-card browse grid. `mark` has no branch for an
 * already-watched movie (`movie-state-controls.tsx:302-335`), so a watched card
 * would offer `Mark watched` again beside a state line that already says
 * `Watched`.
 */
const CATALOG_CONTROLS: MovieStateControlSet = [
  { kind: "watchlist", mode: "toggle" },
  { kind: "watched", mode: "final" },
];

export function CatalogGrid({
  items,
  userId,
  label,
  hrefFor,
  onCommitted,
}: {
  items: readonly CatalogItem[];
  userId: number;
  label: string;
  hrefFor: (item: CatalogItem) => string;
  onCommitted?: (state: MovieState) => void;
}) {
  // One region for the whole grid rather than one per cell. A committed change
  // shows itself visually as a pressed, relabelled control; a reader who cannot
  // see that relabelling gets the same sentence every other surface says, and
  // forty live regions to monitor is not the way to give it to them.
  const [announcement, setAnnouncement] = useState("");

  return (
    <>
      <p aria-live="polite" className="visually-hidden">
        {announcement}
      </p>
      <div aria-label={label} className="catalog-grid" role="list">
        {items.map((item, index) => (
          <div className="catalog-cell" key={item.movie_id} role="listitem">
            <PosterCard
              href={hrefFor(item)}
              metadataNote={metadataNote(item)}
              movie={catalogItemToCard(item)}
              priority={index < PRIORITY_CELLS}
            />
            <CatalogCardActions
              item={item}
              onAnnounce={setAnnouncement}
              onCommitted={onCommitted}
              userId={userId}
            />
          </div>
        ))}
      </div>
    </>
  );
}

function CatalogCardActions({
  item,
  userId,
  onCommitted,
  onAnnounce,
}: {
  item: CatalogItem;
  userId: number;
  onCommitted?: (state: MovieState) => void;
  onAnnounce?: (message: string) => void;
}) {
  const title = displayTitle(item.title, item.release_year);
  // What the viewer last pressed, so the commit can be described. Held as a ref
  // because it is read in an async continuation, not during a render.
  const intent = useRef<MovieStateAction | null>(null);
  // Memoised because the hook's `run` is derived from it: an inline handler
  // would give every cell in the grid a new write path on every render.
  const commit = useCallback(
    (committed: MovieState) => {
      onCommitted?.(committed);
      const action = intent.current;
      if (!action) return;
      onAnnounce?.(
        movieStateAnnouncement({ kind: "committed", action }, { title, voice: "detail" }),
      );
    },
    [onAnnounce, onCommitted, title],
  );
  const { display, pending, message, run, state } = useMovieState({
    userId,
    movieId: item.movie_id,
    title,
    initialState: item.state,
    onCommitted: commit,
  });
  // Read from the committed record rather than the optimistic one on purpose:
  // the buttons are what runs ahead of the server, and a supplementary note
  // that also guessed would have nothing left to correct against.
  const note = stateNote(state);

  return (
    // The control labels are the product's shared ones — "Watchlist", "Mark
    // watched" — which name the action and not the movie. In a grid of forty
    // cards that is ambiguous on its own, so the row is a named group and the
    // movie is announced on the way in.
    <div
      aria-label={`Actions for ${title}`}
      className="catalog-cell-actions"
      role="group"
    >
      <MovieStateControls
        busy={pending !== null}
        classNames={{ root: "catalog-cell-controls", action: "catalog-cell-action" }}
        compact
        controls={CATALOG_CONTROLS}
        idPrefix={`catalog-${item.movie_id}`}
        onAction={(action, control) => {
          intent.current = action;
          void run(action, control);
        }}
        pending={pending}
        state={display}
        title={title}
      />
      {note ? <span className="catalog-cell-state">{note}</span> : null}
      {message?.tone === "note" ? (
        // A refused transition, announced politely: the API declined the write
        // and said why, so nothing here is broken and nothing is worth
        // interrupting a reader mid-grid for.
        <span className="catalog-cell-note" role="status">
          <span aria-hidden="true">Not allowed</span>
          <span className="visually-hidden">{message.text}</span>
        </span>
      ) : null}
      {message?.tone === "error" ? (
        // Short on screen so a failed save cannot reflow the grid, complete in
        // the accessibility tree so the reason is never hover-only.
        <span className="catalog-cell-error" role="alert">
          <span aria-hidden="true">Not saved</span>
          <span className="visually-hidden">{message.text}</span>
        </span>
      ) : null}
    </div>
  );
}
