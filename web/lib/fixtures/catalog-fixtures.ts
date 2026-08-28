/**
 * A recorded catalog in the shape the Bundle 3 endpoint actually returns.
 *
 * Bundle 4's fixtures describe cards; this one describes the *contract* —
 * `CatalogItem` records with metadata source and status, a durable state
 * overlay, and enough titles to produce several real cursor pages. That
 * matters because the interesting Browse behaviour lives in the paging: an
 * append that must not duplicate, an ordering that must not reshuffle, and a
 * cursor that must be rejected when the query underneath it changes.
 *
 * A fixture that hands back one page cannot exercise any of that, so this one
 * is served through the same query engine semantics as the endpoint: filter
 * composition, three sort orders, and an opaque cursor bound to the query
 * fingerprint it was issued for.
 *
 * The metadata mix is deliberate and mirrors the reviewed snapshot: a minority
 * of titles are poster- and synopsis-backed, and the rest exercise the partial
 * and unavailable fallbacks on purpose. It is a test input — the fixture gate
 * in `lib/resources/fixture-gate.ts` and the isolated-preview check are what
 * keep it out of a production read.
 */

import type {
  CatalogItem,
  CatalogResponse,
  MovieDetailItem,
  MovieDetails,
  MovieState,
} from "@/lib/api";

type FixtureRow = readonly [
  movieId: number,
  title: string,
  releaseYear: number,
  genres: readonly string[],
  posterUrl: string | null,
  overview: string | null,
];

/**
 * Poster-backed titles reuse the nine committed placeholder posters; every
 * other title is intentionally without artwork so the deterministic fallback
 * is the common case in evidence, exactly as it is in the reviewed snapshot.
 */
const ROWS: readonly FixtureRow[] = [
  [101, "The Handmaiden", 2016, ["Thriller", "Drama"], "/posters/handmaiden.svg",
    "A con artist enters a secluded estate and discovers that every plan has another plan inside it."],
  [102, "In the Mood for Love", 2000, ["Romance", "Drama"], "/posters/in-the-mood.svg",
    "Two neighbours form a quiet bond after making the same discovery."],
  [103, "Memories of Murder", 2003, ["Crime", "Mystery"], "/posters/memories.svg",
    "Detectives chase a pattern through a rain-soaked rural province."],
  [104, "Portrait of a Lady on Fire", 2019, ["Drama", "Romance"], "/posters/portrait.svg",
    "A painter and her subject see each other with uncommon clarity."],
  [105, "Perfect Blue", 1997, ["Animation", "Thriller"], "/posters/perfect-blue.svg",
    "A performer loses her footing between image, memory, and reality."],
  [106, "Moonlight", 2016, ["Drama"], "/posters/moonlight.svg",
    "Three chapters trace a young man becoming himself."],
  [107, "The Worst Person in the World", 2021, ["Comedy", "Drama"], "/posters/worst-person.svg",
    "A restless search for a life that feels chosen rather than inherited."],
  [108, "Burning", 2018, ["Mystery", "Drama"], "/posters/burning.svg",
    "An old acquaintance, a new stranger, and a disappearance refuse to line up."],
  [109, "A Separation", 2011, ["Drama"], null,
    "One family decision opens a knot of obligation and truth."],
  [110, "Decision to Leave", 2022, ["Mystery", "Romance"], "/posters/decision.svg",
    "A detective finds suspicion and longing difficult to separate."],

  [111, "Children of Men", 2006, ["Sci-Fi", "Thriller"], null,
    "A weary bureaucrat is asked to escort the first pregnancy in eighteen years."],
  [112, "The Conversation", 1974, ["Crime", "Mystery"], null,
    "A surveillance expert begins to hear his own life in someone else's recording."],
  [113, "Paprika", 2006, ["Animation", "Sci-Fi"], null,
    "A stolen device lets other people's dreams leak into waking hours."],
  [114, "Under the Skin", 2013, ["Sci-Fi", "Drama"], null,
    "A visitor drives a Scottish coastline collecting men, and slowly notices herself."],
  [115, "Zodiac", 2007, ["Crime", "Mystery"], null,
    "An obsession outlives the case that started it."],
  [116, "Arrival", 2016, ["Sci-Fi", "Drama"], null,
    "A linguist is asked to translate a language that does not run forwards."],
  [117, "The Lives of Others", 2006, ["Drama", "Thriller"], null,
    "A surveillance officer starts listening to the people he was sent to break."],
  [118, "Chungking Express", 1994, ["Romance", "Comedy"], null,
    "Two heartbreaks share a city, a snack bar, and a run of small coincidences."],
  [119, "Spirited Away", 2001, ["Animation", "Fantasy"], null,
    "A girl takes a job in a bathhouse to buy back her parents."],
  [120, "Heat", 1995, ["Crime", "Action"], null,
    "A detective and a thief recognise each other's discipline."],
  [121, "Aftersun", 2022, ["Drama"], null,
    "A holiday, remembered twenty years later, keeps rearranging itself."],
  [122, "The Handmaid's Errand", 2015, ["Drama", "Mystery"], null,
    "A courier's routine delivery turns into an inventory of everything she has agreed to."],
  [123, "Drive My Car", 2021, ["Drama"], null,
    "A director rehearses a play while learning how to say what he could not."],

  [124, "Ran", 1985, ["Drama", "Action"], null, null],
  [125, "Sunset Boulevard", 1950, ["Drama", "Crime"], null, null],
  [126, "The Third Man", 1949, ["Mystery", "Thriller"], null, null],
  [127, "Stalker", 1979, ["Sci-Fi", "Drama"], null, null],
  [128, "Wings of Desire", 1987, ["Romance", "Drama"], null, null],
  [129, "Do the Right Thing", 1989, ["Drama", "Comedy"], null, null],
  [130, "Blade Runner", 1982, ["Sci-Fi", "Thriller"], null, null],
  [131, "Rear Window", 1954, ["Mystery", "Thriller"], null, null],
  [132, "My Neighbour Totoro", 1988, ["Animation", "Children"], null, null],
  [133, "Fargo", 1996, ["Crime", "Comedy"], null, null],
  [134, "Groundhog Day", 1993, ["Comedy", "Romance"], null, null],
  [135, "Before Sunrise", 1995, ["Romance", "Drama"], null, null],
  [136, "The Big Lebowski", 1998, ["Comedy", "Crime"], null, null],
  [137, "Yi Yi", 2000, ["Drama"], null, null],
  [138, "Amelie", 2001, ["Romance", "Comedy"], null, null],
  [139, "City of God", 2002, ["Crime", "Drama"], null, null],
  [140, "Oldboy", 2003, ["Thriller", "Mystery"], null, null],
  [141, "The Incredibles", 2004, ["Animation", "Action"], null, null],
  [142, "Pan's Labyrinth", 2006, ["Fantasy", "Drama"], null, null],
  [143, "There Will Be Blood", 2007, ["Drama"], null, null],
  [144, "Let the Right One In", 2008, ["Horror", "Romance"], null, null],
  [145, "A Prophet", 2009, ["Crime", "Drama"], null, null],
  [146, "The Social Network", 2010, ["Drama"], null, null],
  [147, "Take Shelter", 2011, ["Drama", "Thriller"], null, null],
  [148, "Holy Motors", 2012, ["Fantasy", "Drama"], null, null],
  [149, "Inside Llewyn Davis", 2013, ["Drama", "Comedy"], null, null],
  [150, "Whiplash", 2014, ["Drama"], null, null],
  [151, "The Assassin", 2015, ["Action", "Drama"], null, null],
  [152, "Lady Bird", 2017, ["Comedy", "Drama"], null, null],
  [153, "Shoplifters", 2018, ["Drama", "Crime"], null, null],
  [154, "Parasite", 2019, ["Thriller", "Drama"], null, null],
];

const TENANT_ID = "demo";
export const RECORDED_CATALOG_USER_ID = 900000101;

/** Deterministic so `popular` ordering and screenshots never drift. */
function interactionCount(movieId: number): number {
  return ((movieId * 37) % 97) + 3;
}

function metadata(row: FixtureRow): Pick<CatalogItem, "metadata_source" | "source_status"> {
  const [, , , , posterUrl, overview] = row;
  if (posterUrl && overview) {
    return { metadata_source: "reviewed-fixture", source_status: "complete" };
  }
  if (posterUrl || overview) {
    return { metadata_source: "tmdb-snapshot", source_status: "partial" };
  }
  return { metadata_source: "movielens", source_status: "unavailable" };
}

function recordedState(
  movieId: number,
  overrides: Partial<MovieState>,
): MovieState {
  return {
    tenant_id: TENANT_ID,
    user_id: RECORDED_CATALOG_USER_ID,
    movie_id: movieId,
    rating: null,
    rating_updated_at: null,
    watched_at: null,
    watchlisted_at: null,
    dismissed_at: null,
    revision: 1,
    updated_at: "2026-08-20T12:00:00Z",
    ...overrides,
  };
}

/** A handful of titles carry durable state so the overlay is visible. */
const STATE_OVERRIDES: Record<number, Partial<MovieState>> = {
  101: { watchlisted_at: "2026-08-18T09:00:00Z" },
  103: { watched_at: "2026-08-14T21:30:00Z", rating: 4.5, rating_updated_at: "2026-08-14T21:35:00Z", revision: 3 },
  104: { watched_at: "2026-08-02T20:10:00Z", rating: 5, rating_updated_at: "2026-08-02T20:12:00Z", revision: 2 },
  106: { watchlisted_at: "2026-08-19T18:45:00Z" },
  108: { watched_at: "2026-07-30T19:00:00Z", rating: 4, rating_updated_at: "2026-07-30T19:05:00Z", revision: 4 },
  110: { watchlisted_at: "2026-08-20T08:15:00Z" },
  130: { watched_at: "2026-06-11T22:00:00Z", revision: 1 },
  140: { dismissed_at: "2026-08-12T10:00:00Z", revision: 2 },
};

/**
 * The enriched detail block, on the three titles that need to prove something.
 *
 * Only detail carries `details` in the contract, and only a handful of titles
 * carry it here — which is the honest mix, since the reviewed snapshot is
 * enriched for a minority of the catalog. The three are chosen to cover the
 * branches the page has: the whole record with a trailer and a backdrop, a
 * record with neither, and (by omission) the far more common `details`-less
 * item that has to render exactly the page this route rendered before.
 *
 * Artwork is local on purpose. A fixture that pointed at `image.tmdb.org` would
 * make the isolated preview reach a third party on every screenshot, which is
 * the opposite of what the harness is for.
 */
const DETAILS: Record<number, MovieDetails> = {
  // Everything present: backdrop, tagline, runtime, score, six cast, trailer.
  101: {
    tagline: "Two women. Two cons. One estate that keeps them both.",
    runtime_minutes: 145,
    release_date: "2016-06-01",
    backdrop_url: "/backdrops/handmaiden.svg",
    tmdb_rating: { average: 8.1, count: 4812 },
    directors: ["Park Chan-wook"],
    cast: [
      { name: "Kim Min-hee", character: "Lady Hideko", profile_url: "/profiles/cast-a.svg" },
      { name: "Kim Tae-ri", character: "Sook-hee", profile_url: "/profiles/cast-b.svg" },
      { name: "Ha Jung-woo", character: "Count Fujiwara", profile_url: null },
      { name: "Cho Jin-woong", character: "Uncle Kouzuki", profile_url: null },
      { name: "Kim Hae-sook", character: "Mrs. Sasaki", profile_url: null },
      { name: "Moon So-ri", character: "Aunt Hideko", profile_url: null },
    ],
    trailer: { provider: "youtube", key: "T7kfW4trvUM", name: "Official Trailer" },
    fetched_at: "2026-08-24T09:00:00Z",
  },
  // Enriched but with no trailer and no backdrop: the hero degrades to the
  // poster-left layout while the credits and the score still render.
  103: {
    tagline: "A pattern nobody can finish reading.",
    runtime_minutes: 132,
    release_date: "2003-05-02",
    backdrop_url: null,
    tmdb_rating: { average: 8.0, count: 1937 },
    directors: ["Bong Joon-ho"],
    cast: [
      { name: "Song Kang-ho", character: "Detective Park", profile_url: "/profiles/cast-b.svg" },
      { name: "Kim Sang-kyung", character: "Detective Seo", profile_url: null },
      { name: "Kim Roi-ha", character: "Detective Cho", profile_url: null },
    ],
    trailer: null,
    fetched_at: "2026-08-24T09:00:00Z",
  },
  // Enriched and already rated, which is the state the rating chip renders in.
  104: {
    tagline: "Look at me the way I am looking at you.",
    runtime_minutes: 122,
    release_date: "2019-09-18",
    backdrop_url: "/backdrops/portrait.svg",
    tmdb_rating: { average: 8.3, count: 2604 },
    directors: ["Céline Sciamma"],
    cast: [
      { name: "Noémie Merlant", character: "Marianne", profile_url: "/profiles/cast-a.svg" },
      { name: "Adèle Haenel", character: "Héloïse", profile_url: null },
      { name: "Luàna Bajrami", character: "Sophie", profile_url: null },
    ],
    trailer: { provider: "youtube", key: "R-fQPTwma9o", name: "Official Trailer" },
    fetched_at: "2026-08-24T09:00:00Z",
  },
};

export const RECORDED_CATALOG: readonly CatalogItem[] = ROWS.map((row) => {
  const [movieId, title, releaseYear, genres, posterUrl, overview] = row;
  const override = STATE_OVERRIDES[movieId];
  return {
    movie_id: movieId,
    title,
    genres: [...genres],
    tmdb_id: posterUrl ? String(200000 + movieId) : null,
    release_year: releaseYear,
    poster_url: posterUrl,
    overview,
    ...metadata(row),
    state: override ? recordedState(movieId, override) : null,
    interaction_count: interactionCount(movieId),
  };
});

/**
 * Detail returns the enriched block; the list endpoint never does. Keeping that
 * split in the fixture is what makes the preview harness able to catch a Browse
 * card that starts reading a field the grid response will not carry.
 */
export function recordedCatalogItem(movieId: number): MovieDetailItem | undefined {
  const item = RECORDED_CATALOG.find((entry) => entry.movie_id === movieId);
  if (!item) return undefined;
  return { ...item, details: DETAILS[movieId] ?? null };
}

/** Mirrors the endpoint's normalized sort title: leading articles drop out. */
export function sortTitle(title: string): string {
  return title.replace(/^(the|a|an)\s+/i, "").toLowerCase();
}

export type RecordedCatalogQuery = {
  q: string | null;
  genre: string | null;
  yearFrom: number | null;
  yearTo: number | null;
  sort: "title" | "newest" | "popular";
  limit: number;
  cursor: string | null;
};

export class RecordedCursorRejected extends Error {
  constructor() {
    super("catalog cursor is invalid for this query");
    this.name = "RecordedCursorRejected";
  }
}

/** FNV-1a: short, stable, and dependency-free. Not a security boundary. */
function fingerprint(query: RecordedCatalogQuery): string {
  const payload = JSON.stringify([
    query.q?.toLowerCase() ?? null,
    query.genre ?? null,
    query.yearFrom,
    query.yearTo,
    query.sort,
  ]);
  let hash = 0x811c9dc5;
  for (let index = 0; index < payload.length; index += 1) {
    hash ^= payload.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function encodeCursor(input: {
  fingerprint: string;
  sortValue: string | number;
  movieId: number;
}): string {
  return btoa(
    JSON.stringify({ v: 1, q: input.fingerprint, s: input.sortValue, id: input.movieId }),
  )
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function decodeCursor(
  value: string,
  expected: string,
): { sortValue: string | number; movieId: number } {
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/");
    const parsed: unknown = JSON.parse(
      atob(padded + "=".repeat((4 - (padded.length % 4)) % 4)),
    );
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      (parsed as { v?: unknown }).v !== 1 ||
      (parsed as { q?: unknown }).q !== expected ||
      typeof (parsed as { id?: unknown }).id !== "number"
    ) {
      throw new RecordedCursorRejected();
    }
    const sortValue = (parsed as { s?: unknown }).s;
    if (typeof sortValue !== "string" && typeof sortValue !== "number") {
      throw new RecordedCursorRejected();
    }
    return { sortValue, movieId: (parsed as { id: number }).id };
  } catch {
    throw new RecordedCursorRejected();
  }
}

function sortValueOf(item: CatalogItem, sort: RecordedCatalogQuery["sort"]) {
  if (sort === "title") return sortTitle(item.title);
  if (sort === "newest") return item.release_year ?? 0;
  return item.interaction_count;
}

function compare(
  left: CatalogItem,
  right: CatalogItem,
  sort: RecordedCatalogQuery["sort"],
): number {
  const leftValue = sortValueOf(left, sort);
  const rightValue = sortValueOf(right, sort);
  if (leftValue !== rightValue) {
    if (sort === "title") return leftValue < rightValue ? -1 : 1;
    return leftValue < rightValue ? 1 : -1;
  }
  // Movie ID ascending is the endpoint's tie-breaker, and it is what makes the
  // ordering total — without it a cursor could skip or repeat rows.
  return left.movie_id - right.movie_id;
}

function afterCursor(
  item: CatalogItem,
  sort: RecordedCatalogQuery["sort"],
  cursor: { sortValue: string | number; movieId: number },
): boolean {
  const value = sortValueOf(item, sort);
  if (value === cursor.sortValue) return item.movie_id > cursor.movieId;
  if (sort === "title") return value > cursor.sortValue;
  return value < cursor.sortValue;
}

export function queryRecordedCatalog(query: RecordedCatalogQuery): CatalogResponse {
  const expected = fingerprint(query);
  const cursor = query.cursor ? decodeCursor(query.cursor, expected) : null;
  const search = query.q?.trim().toLowerCase() ?? "";

  const matches = RECORDED_CATALOG.filter((item) => {
    if (search && !item.title.toLowerCase().includes(search)) return false;
    if (query.genre && !item.genres.includes(query.genre)) return false;
    if (query.yearFrom !== null && (item.release_year ?? 0) < query.yearFrom) return false;
    if (query.yearTo !== null && (item.release_year ?? 0) > query.yearTo) return false;
    return true;
  })
    .slice()
    .sort((left, right) => compare(left, right, query.sort))
    .filter((item) => (cursor ? afterCursor(item, query.sort, cursor) : true));

  const page = matches.slice(0, query.limit);
  const hasMore = matches.length > query.limit;
  const last = page[page.length - 1];

  return {
    tenant_id: TENANT_ID,
    user_id: RECORDED_CATALOG_USER_ID,
    items: page,
    page: {
      has_more: hasMore,
      next_cursor:
        hasMore && last
          ? encodeCursor({
              fingerprint: expected,
              sortValue: sortValueOf(last, query.sort),
              movieId: last.movie_id,
            })
          : null,
    },
  };
}
