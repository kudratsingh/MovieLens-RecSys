/**
 * How the enriched TMDB record on a movie's own page is read out loud.
 *
 * The types come from the generated contract (`MovieDetailItem` carries
 * `details`; `CatalogItem`, the list type, does not — a Browse page of forty
 * titles dragging forty cast lists through the response is a page-size
 * regression the API declines to make possible). What lives here is the
 * formatting: a runtime in hours and minutes, a score with its vote count, an
 * embed URL, a monogram for a missing portrait. Each of them is a product
 * decision with a rule behind it, and each is used in more than one place.
 */

import type { MovieTrailer, TmdbRating } from "@/lib/api";

export type {
  MovieCastMember,
  MovieDetailItem,
  MovieDetails,
  MovieTrailer,
  TmdbRating,
} from "@/lib/api";

/** "2h 25m", "48m". Null when the record does not hold a usable runtime. */
export function runtimeText(minutes: number | null | undefined): string | null {
  if (typeof minutes !== "number" || !Number.isFinite(minutes) || minutes <= 0) {
    return null;
  }
  const whole = Math.round(minutes);
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  if (hours === 0) return `${rest}m`;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

/**
 * "7.8 / 10 · 4,812 ratings".
 *
 * The count travels with the average because an 8.4 from nine people and an 8.4
 * from nine thousand are not the same claim. A score with no votes behind it is
 * therefore not a score at all: it reads as no rating rather than as "0 ratings"
 * beside an average nobody gave. The locale is pinned so the thousands
 * separator is the same in a screenshot as it is on a runner.
 */
export function tmdbScoreText(rating: TmdbRating | null | undefined): string | null {
  if (!rating || !Number.isFinite(rating.average) || rating.count <= 0) return null;
  const votes = rating.count.toLocaleString("en-US");
  return `${rating.average.toFixed(1)} / 10 · ${votes} ${rating.count === 1 ? "rating" : "ratings"}`;
}

/**
 * The privacy-enhanced embed, built only when the viewer asks for one.
 *
 * `youtube-nocookie.com` is the host that sets no tracking cookie for a viewer
 * who never plays anything, and the URL is assembled here rather than inline in
 * the component so the promise the page makes — nothing third-party until the
 * press — has exactly one implementation to audit. The key is interpolated into
 * a URL, so it is escaped here even though it is validated where it is written.
 */
export function trailerEmbedUrl(trailer: MovieTrailer): string {
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(trailer.key)}?autoplay=1&rel=0`;
}

/**
 * The monogram shown where a cast portrait is missing.
 *
 * `posterInitials` is the wrong rule for a person: it drops articles, which is
 * a title's problem, and it would give "KM" for "Kim Min-hee" and "KM" again
 * for "Kim Min-jung" only by accident. First and last initial is the convention
 * a reader already knows, and a mononym gives one letter rather than two from
 * the same word — the same reasoning the poster rule applies to a one-word
 * title.
 */
export function personInitials(name: string): string {
  const words = name.split(/\s+/).filter((word) => /[\p{L}\p{N}]/u.test(word));
  if (words.length === 0) return "?";
  const first = letterOf(words[0]);
  const last = words.length > 1 ? letterOf(words[words.length - 1]) : "";
  return `${first}${last}` || "?";
}

function letterOf(word: string): string {
  const match = /[\p{L}\p{N}]/u.exec(word);
  return match ? match[0].toUpperCase() : "";
}

/** "4", "4.5" — a whole star does not print a trailing zero. */
export function ratingValueText(rating: number): string {
  return Number.isInteger(rating) ? String(rating) : rating.toFixed(1);
}
