/**
 * Catalog contract → card presentation, including the metadata fallbacks.
 *
 * The shared metadata snapshot is knowingly incomplete: of the reviewed demo
 * catalog, a minority of titles have a poster and a synopsis and the rest
 * deliberately exercise the gaps. The product answer is a deterministic,
 * source-aware fallback computed from data we already hold — never a live
 * TMDB lookup per card, which is exactly the fan-out the local snapshot
 * exists to prevent.
 *
 * "Deterministic" is doing real work in that sentence: the same movie must
 * produce the same fallback on every render, in every viewport, and in a
 * screenshot taken a month later, or the evidence matrix stops meaning
 * anything.
 */

import type { CatalogItem, MovieState } from "@/lib/api";
import type { MovieCard, MovieState as CardState } from "@/lib/movie-types";

type MetadataSource = CatalogItem["metadata_source"];
type SourceStatus = CatalogItem["source_status"];

const SOURCE_LABEL: Record<MetadataSource, string> = {
  "reviewed-fixture": "Reviewed snapshot",
  "tmdb-snapshot": "TMDB snapshot",
  movielens: "MovieLens catalog",
};

const STATUS_LABEL: Record<SourceStatus, string> = {
  complete: "Complete details",
  partial: "Partial details",
  unavailable: "Details unavailable",
};

const MISSING_OVERVIEW: Record<SourceStatus, string> = {
  complete: "No synopsis is recorded for this title.",
  partial: "A synopsis is not part of the reviewed metadata snapshot for this title yet.",
  unavailable:
    "Only MovieLens title, year, and genre data is available for this title. There is no synopsis to show.",
};

/** Full provenance line: shown on detail, and in the card's accessible name. */
export function metadataSummary(item: CatalogItem): string {
  return `${SOURCE_LABEL[item.metadata_source]} · ${STATUS_LABEL[item.source_status]}`;
}

/** Compact grid note. A complete record needs no explanation. */
export function metadataNote(item: CatalogItem): string | null {
  return item.source_status === "complete" ? null : STATUS_LABEL[item.source_status];
}

export function overviewText(item: CatalogItem): string {
  return item.overview?.trim() || MISSING_OVERVIEW[item.source_status];
}

export function releaseYearText(item: CatalogItem): string {
  return item.release_year === null ? "Year unavailable" : String(item.release_year);
}

export function genresText(item: CatalogItem): string {
  return item.genres.length ? item.genres.join(" · ") : "Genre unavailable";
}

export function cardState(state: MovieState | null): CardState {
  return {
    watched: Boolean(state?.watched_at),
    watchlisted: Boolean(state?.watchlisted_at),
    rating: state?.rating ?? null,
    suppressed: Boolean(state?.dismissed_at),
  };
}

/**
 * Poster alternative text policy: a poster that loads is decorative next to a
 * visible title, so it takes an empty alt and the link carries the name. When
 * there is no poster the fallback renders the title itself, so nothing is
 * lost either way.
 */
export function catalogItemToCard(item: CatalogItem): MovieCard {
  return {
    id: item.movie_id,
    title: item.title,
    year: item.release_year,
    genres: item.genres,
    posterSrc: item.poster_url,
    posterAlt: "",
    overview: item.overview,
    state: cardState(item.state),
  };
}

/** Short, factual state line for a card: no model claim is implied. */
export function stateNote(state: MovieState | null): string | null {
  if (!state) return null;
  if (state.rating !== null) return `Rated ${state.rating.toFixed(1)}`;
  if (state.watched_at) return "Watched";
  if (state.watchlisted_at) return "In watchlist";
  return null;
}
