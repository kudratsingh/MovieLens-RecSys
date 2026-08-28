/**
 * The presentational vocabulary every movie surface shares.
 *
 * The two functions below are here rather than beside one route's mapper for
 * the same reason `lib/movie-state/` exists: the product had three answers to
 * "what is this movie called" and three answers to "what mark stands in for a
 * missing poster", so the same film printed `Babe (1995)` over a `1995` line on
 * one route and `Babe` on another, and showed `B(`, `Ba`, and `B` as its
 * fallback. A shared rule is the only thing that makes those agree.
 */

import type { MovieDisplayState } from "@/lib/movie-state/actions";

/** MovieLens titles carry their year in a trailing parenthetical. */
export const TRAILING_YEAR = /\s*\((\d{4})\)\s*$/;

/**
 * MovieLens titles embed the release year. Showing "Heat (1995)" above a "1995"
 * metadata line reads like a bug, so the year is dropped from the title only
 * when it is also available as structured metadata — a trailing parenthetical
 * that disagrees with the structured year is part of the name (a re-release
 * marker, a disambiguator) and is left alone.
 */
export function displayTitle(title: string, releaseYear: number | null): string {
  if (releaseYear === null) return title;
  const match = TRAILING_YEAR.exec(title);
  return match && Number(match[1]) === releaseYear
    ? title.slice(0, match.index).trim()
    : title;
}

/**
 * Articles and prepositions carry no identity, so "The Handmaiden" is "H" and
 * not "TH". Kept small and closed on purpose: this is a legibility rule, not a
 * stop-word list for search.
 */
const INITIAL_STOP_WORDS = new Set(["the", "a", "an", "of", "in", "to", "and"]);

/** "(1995)", "—", "&": tokens that open with punctuation are not words. */
const OPENS_WITH_PUNCTUATION = /^[^\p{L}\p{N}]/u;
const EDGE_PUNCTUATION = /^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu;

/**
 * The mark a poster frame shows when there is no artwork.
 *
 * Deterministic by construction — same title, same mark, on every route, in
 * every viewport, and in a screenshot taken a month later — which is what lets
 * the evidence matrix compare captures at all. Feed it the *display* title:
 * running it over the raw MovieLens title is what produced marks like `B(`,
 * because the second word of a one-word title is `(1995)`.
 */
export function posterInitials(title: string): string {
  const words = title
    .split(/\s+/)
    .filter((word) => word.length > 0 && !OPENS_WITH_PUNCTUATION.test(word))
    .filter(
      (word) => !INITIAL_STOP_WORDS.has(word.replace(EDGE_PUNCTUATION, "").toLowerCase()),
    );

  // A one-word title yields one letter rather than two from the same word:
  // "Ba" reads like a truncation, "B" reads like a monogram.
  const mark = words
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join("");

  // Nothing survived — an empty title, or one made only of stop words. A "?"
  // is honest about that; a blank frame looks like a rendering bug.
  return mark || "?";
}

export type MovieCard = {
  id: number;
  title: string;
  year: number | null;
  genres: readonly string[];
  posterSrc: string | null;
  posterAlt: string;
  overview: string | null;
  reason?: string;
  rank?: number;
  /**
   * The same projection the controls render, so a card and the button beside it
   * cannot disagree about whether a movie is saved.
   */
  state: MovieDisplayState;
};

export type EvidenceRecord = {
  policy: string;
  modelVersion: string;
  candidateVersion: string;
  featureVersion: string;
  requestId: string;
  latencyMs: number;
  fallbackReason: string | null;
};

export type ResourceName =
  | "recommendations"
  | "catalog"
  | "library"
  | "evidence";

export type ResourceResult<T> =
  | { status: "ready"; data: T; source: "recorded-contract-fixture" }
  | { status: "error"; message: string; source: "recorded-contract-fixture" };
