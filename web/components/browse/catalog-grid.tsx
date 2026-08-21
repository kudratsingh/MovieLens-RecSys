"use client";

/**
 * The Browse poster grid.
 *
 * Two properties are load-bearing here and both are about the grid not moving
 * under the viewer. Every cell reserves its poster at a fixed 2:3 ratio, so a
 * poster that 404s or times out swaps to the deterministic fallback inside the
 * space already allocated — nothing below it shifts. And a card's secondary
 * action reconciles state in place: a watchlist toggle changes the card's
 * label, never its position in the order the endpoint returned.
 *
 * Posters below the fold stay lazy (Next's default) and only the first cells
 * are marked priority, because the grid is the largest contentful paint on
 * this route and everything after the first row is a scroll away.
 */

import { PosterCard } from "@/components/movie/poster-card";
import { useCanonicalMovieState } from "@/components/movie/use-canonical-movie-state";
import { Icon } from "@/components/ui/icons";
import type { CatalogItem, MovieState } from "@/lib/api";
import {
  catalogItemToCard,
  metadataNote,
  stateNote,
} from "@/lib/browse/catalog-card";
import "./catalog-grid.css";

/** Only the first row is eager; the rest of the grid stays lazy. */
const PRIORITY_CELLS = 2;

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
  return (
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
            onCommitted={onCommitted}
            userId={userId}
          />
        </div>
      ))}
    </div>
  );
}

function CatalogCardActions({
  item,
  userId,
  onCommitted,
}: {
  item: CatalogItem;
  userId: number;
  onCommitted?: (state: MovieState) => void;
}) {
  const { display, pending, message, run, state } = useCanonicalMovieState({
    userId,
    movieId: item.movie_id,
    title: item.title,
    initialState: item.state,
    onCommitted,
  });
  const note = stateNote(state);

  return (
    <div className="catalog-cell-actions">
      <button
        aria-busy={pending !== null}
        aria-disabled={pending !== null}
        aria-pressed={display.watchlisted}
        className="catalog-cell-watchlist"
        onClick={(event) => {
          if (pending !== null) return;
          const trigger = event.currentTarget;
          void run(
            { resource: "watchlist", method: display.watchlisted ? "DELETE" : "PUT" },
            trigger,
          );
        }}
        type="button"
      >
        <Icon name="bookmark" />
        <span className="visually-hidden">
          {display.watchlisted ? "Remove" : "Add"} {item.title}{" "}
          {display.watchlisted ? "from" : "to"} watchlist
        </span>
        <span aria-hidden="true">
          {display.watchlisted ? "Saved" : "Watchlist"}
        </span>
      </button>
      {note ? <span className="catalog-cell-state">{note}</span> : null}
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
