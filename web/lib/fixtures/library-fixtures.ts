/**
 * Recorded Library data for the `/ui-preview` surface, component tests, and the
 * screenshot harness.
 *
 * `/ui-preview` is the route family that advertises itself as recorded, so this
 * is a test input rather than a fallback: no live route imports it, the live
 * client (`lib/library/client.ts`) imports no fixture, and
 * `lib/resources/server.ts` remains structurally unable to reach recorded data.
 * Everything produced here is tagged `recorded-contract-fixture` so the source
 * is visible in the UI and assertable in a test.
 *
 * The recorded client is a small working implementation rather than a set of
 * frozen responses. Filtering, sorting, cursor pages, and canonical mutations
 * all behave, which is what lets the preview exercise the same reconciliation
 * path the live route uses — including the states that only appear after a
 * write.
 */

import type {
  LibraryMovie,
  LibraryResponse,
  MovieDetailResponse,
  MovieDetails,
  MovieState,
  TasteSummaryResponse,
} from "@/lib/api";
import type { LibraryClient, LibraryReadOptions } from "@/lib/library/client";
import { movieMatchesTab } from "@/lib/library/collection";
import {
  applyActionToState,
  movieStateActionOf,
} from "@/lib/movie-state/actions";
import type { LibraryTab } from "@/lib/library/url-state";
import { FIXTURE_REQUEST_ID } from "@/lib/resources/fixture-gate";
import {
  emptyState,
  failureState,
  readyState,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";

const TENANT_ID = "demo";
const USER_ID = 900000101;

export const RECORDED_PERSONA = "Action Fan";

type Seed = {
  movieId: number;
  title: string;
  genres: string[];
  year: number;
  /** The same local artwork the shared movie fixtures use for this id. */
  poster?: string;
  /** The crowd average, on the minority of titles the snapshot has covered. */
  tmdb?: number;
  watchedAt?: string;
  watchlistedAt?: string;
  rating?: number;
  dismissedAt?: string;
};

/*
 * Poster coverage here is deliberately partial, and split by tab rather than
 * scattered: Rated and History carry artwork for most rows, and Watchlist
 * carries none. That is what makes one capture prove the row's poster treatment
 * and another prove the shared fallback mark, instead of the matrix depending
 * on which rows happened to sort into the first page.
 */
const SEEDS: readonly Seed[] = [
  { movieId: 101, title: "The Handmaiden", genres: ["Thriller", "Drama"], tmdb: 8.1, year: 2016, poster: "/posters/handmaiden.svg", watchedAt: "2026-08-18T20:10:00Z", rating: 5 },
  { movieId: 102, title: "In the Mood for Love", genres: ["Romance", "Drama"], year: 2000, poster: "/posters/in-the-mood.svg", watchedAt: "2026-08-17T19:40:00Z", rating: 4.5 },
  { movieId: 103, title: "Memories of Murder", genres: ["Crime", "Mystery"], tmdb: 8.0, year: 2003, poster: "/posters/memories.svg", watchedAt: "2026-08-16T21:05:00Z", rating: 4.5 },
  { movieId: 104, title: "Portrait of a Lady on Fire", genres: ["Drama", "Romance"], tmdb: 8.3, year: 2019, poster: "/posters/portrait.svg", watchedAt: "2026-08-15T18:25:00Z", rating: 5 },
  { movieId: 105, title: "Perfect Blue", genres: ["Animation", "Thriller"], year: 1997, poster: "/posters/perfect-blue.svg", watchedAt: "2026-08-14T22:15:00Z", rating: 4 },
  { movieId: 106, title: "Moonlight", genres: ["Drama"], tmdb: 7.4, year: 2016, poster: "/posters/moonlight.svg", watchedAt: "2026-08-13T20:00:00Z", rating: 4.5 },
  { movieId: 107, title: "The Worst Person in the World", genres: ["Comedy", "Drama"], year: 2021, poster: "/posters/worst-person.svg", watchedAt: "2026-08-12T19:30:00Z", rating: 3.5 },
  { movieId: 108, title: "Burning", genres: ["Mystery", "Drama"], year: 2018, poster: "/posters/burning.svg", watchedAt: "2026-08-11T21:45:00Z", rating: 4 },
  // No artwork in the shared fixtures either, so the mark shows on the first
  // page of Rated at every width.
  { movieId: 109, title: "A Separation", genres: ["Drama"], year: 2011, watchedAt: "2026-08-10T18:05:00Z", rating: 4.5 },
  { movieId: 110, title: "Decision to Leave", genres: ["Mystery", "Romance"], year: 2022, poster: "/posters/decision.svg", watchedAt: "2026-08-09T20:20:00Z", rating: 4 },
  { movieId: 111, title: "Drive My Car", genres: ["Drama"], year: 2021, watchedAt: "2026-08-08T17:50:00Z" },
  { movieId: 112, title: "Parasite", genres: ["Thriller", "Comedy"], tmdb: 8.5, year: 2019, watchedAt: "2026-08-07T22:00:00Z", rating: 5 },
  { movieId: 113, title: "The Wailing", genres: ["Horror", "Mystery"], year: 2016, watchedAt: "2026-08-06T23:10:00Z" },
  { movieId: 114, title: "Oldboy", genres: ["Thriller", "Action"], tmdb: 7.7, year: 2003, watchedAt: "2026-08-05T21:30:00Z", rating: 4 },
  { movieId: 115, title: "Shoplifters", genres: ["Drama"], year: 2018, watchedAt: "2026-08-04T19:15:00Z" },
  { movieId: 116, title: "Aftersun", genres: ["Drama"], year: 2022, watchlistedAt: "2026-08-19T09:00:00Z" },
  { movieId: 117, title: "Past Lives", genres: ["Romance", "Drama"], year: 2023, watchlistedAt: "2026-08-18T08:30:00Z" },
  { movieId: 118, title: "The Zone of Interest", genres: ["Drama", "War"], year: 2023, watchlistedAt: "2026-08-17T07:45:00Z" },
  { movieId: 119, title: "Anatomy of a Fall", genres: ["Crime", "Drama"], year: 2023, watchlistedAt: "2026-08-16T10:20:00Z", dismissedAt: "2026-08-20T11:00:00Z" },
];

function seedState(seed: Seed): MovieState {
  return {
    dismissed_at: seed.dismissedAt ?? null,
    movie_id: seed.movieId,
    rating: seed.rating ?? null,
    rating_updated_at: seed.rating === undefined ? null : (seed.watchedAt ?? null),
    revision: 1,
    tenant_id: TENANT_ID,
    updated_at: seed.watchedAt ?? seed.watchlistedAt ?? "2026-08-01T00:00:00Z",
    user_id: USER_ID,
    watched_at: seed.watchedAt ?? null,
    watchlisted_at: seed.watchlistedAt ?? null,
  };
}

export function recordedLibraryMovies(): LibraryMovie[] {
  return SEEDS.map((seed) => ({
    genres: [...seed.genres],
    movie_id: seed.movieId,
    poster_url: seed.poster ?? null,
    release_year: seed.year,
    state: seedState(seed),
    title: seed.title,
    tmdb_rating: seed.tmdb ?? null,
  }));
}

/**
 * The enriched block the Seen spotlight adds on top of a row it already has.
 *
 * Three titles carry one, which is the honest mix for a snapshot that is
 * enriched for a minority of the catalog: a full record with a backdrop, one
 * without a backdrop but with a runtime and a score, and — by omission — the
 * common case, where the spotlight renders exactly the card it renders while
 * the read is still in flight.
 */
const SEEN_DETAILS: Record<number, MovieDetails> = {
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
    ],
    trailer: null,
    fetched_at: "2026-08-24T09:00:00Z",
  },
  103: {
    tagline: "A pattern nobody can finish reading.",
    runtime_minutes: 132,
    release_date: "2003-05-02",
    backdrop_url: null,
    tmdb_rating: { average: 8.0, count: 1937 },
    directors: ["Bong Joon-ho"],
    cast: [
      { name: "Song Kang-ho", character: "Detective Park", profile_url: null },
      { name: "Kim Sang-kyung", character: "Detective Seo", profile_url: null },
    ],
    trailer: null,
    fetched_at: "2026-08-24T09:00:00Z",
  },
  104: {
    tagline: "Look at me the way I am looking at you.",
    runtime_minutes: 122,
    release_date: "2019-09-18",
    backdrop_url: "/backdrops/portrait.svg",
    tmdb_rating: { average: 8.3, count: 2604 },
    directors: ["Céline Sciamma"],
    cast: [{ name: "Noémie Merlant", character: "Marianne", profile_url: null }],
    trailer: null,
    fetched_at: "2026-08-24T09:00:00Z",
  },
};

export const recordedTasteSummary: TasteSummaryResponse = {
  average_rating: 4.4,
  explanation:
    "Based on ratings in this persona's live library. This summary is not a deployed-model explanation.",
  generated_at: "2026-08-21T12:00:00Z",
  rating_count: 12,
  source: "live-ratings-v1",
  tenant_id: TENANT_ID,
  user_id: USER_ID,
  top_genres: [
    { average_rating: 4.5, genre: "Drama", rated_count: 8 },
    { average_rating: 4.4, genre: "Thriller", rated_count: 4 },
    { average_rating: 4.3, genre: "Mystery", rated_count: 4 },
    { average_rating: 4.5, genre: "Romance", rated_count: 3 },
  ],
};

const RECORDED = "recorded-contract-fixture" as const;

function sortKeyDate(state: MovieState, tab: LibraryTab): string {
  return (tab === "watchlist" ? state.watchlisted_at : state.watched_at) ?? "";
}

/**
 * The view a recorded cursor belongs to.
 *
 * The endpoint binds its cursor to the fingerprint of the tab, sort and filters
 * it was issued under, and rejects a cursor reused against a different query
 * with a `400`. A fixture that happily paged any cursor into any view would
 * leave the route's recovery — drop the cursor, restart from the top, say so —
 * unreachable in the preview harness, which is where that state is captured.
 */
type RecordedView = Pick<
  LibraryReadOptions,
  "tab" | "sort" | "query" | "genre" | "yearFrom" | "yearTo"
>;

function viewFingerprint(options: RecordedView): string {
  return [
    options.tab,
    options.sort,
    options.query.trim().toLowerCase(),
    options.genre ?? "",
    options.yearFrom ?? "",
    options.yearTo ?? "",
  ]
    .join("|")
    .replace(/[^a-z0-9]+/gi, "-");
}

/** The cursor this fixture would issue, so a test can name a page position. */
export function recordedLibraryCursor(options: RecordedView, offset: number): string {
  return `recorded:${offset}:${viewFingerprint(options)}`;
}

/** The offset a cursor carries, or null when it belongs to another view. */
function cursorOffset(options: LibraryReadOptions): number | null {
  if (!options.cursor) return 0;
  const [, offset, fingerprint] = options.cursor.split(":");
  return fingerprint === viewFingerprint(options) ? Number(offset) || 0 : null;
}

export type RecordedLibraryOptions = {
  /** Tabs forced to answer with no rows, for the empty-state captures. */
  emptyTabs?: readonly LibraryTab[];
  /** Resources forced to fail, for the partial-failure captures. */
  failing?: readonly ("library" | "taste-profile")[];
};

function recordedFailure(
  resource: "library" | "taste-profile",
): ResourceFailure {
  return failureState({
    status: "upstream-error",
    resource,
    reason: "server",
    requestId: FIXTURE_REQUEST_ID,
  });
}

/**
 * Builds a working Library client over an in-memory recorded collection.
 *
 * The store is per-client, so a preview session's writes stay in that session
 * and a reload returns to the recorded starting point.
 */
export function createRecordedLibraryClient(
  options: RecordedLibraryOptions = {},
): LibraryClient {
  const movies = new Map(
    recordedLibraryMovies().map((movie) => [movie.movie_id, movie]),
  );
  const emptyTabs = new Set(options.emptyTabs ?? []);
  const failing = new Set(options.failing ?? []);
  let summary = recordedTasteSummary;

  function counts() {
    const all = [...movies.values()];
    return {
      history: all.filter((movie) => movieMatchesTab(movie.state, "history")).length,
      rated: all.filter((movie) => movieMatchesTab(movie.state, "rated")).length,
      watchlist: all.filter((movie) => movieMatchesTab(movie.state, "watchlist")).length,
    };
  }

  /**
   * The recorded ordering, spelled the way the endpoint spells it: every sort
   * ends in `movie_id` so it is a total order, and an absent value sorts below
   * every real one rather than being dropped. Getting that wrong here would
   * make the preview disagree with the service about which title is first,
   * which is the one thing a recorded harness must not do.
   */
  function compare(
    left: LibraryMovie,
    right: LibraryMovie,
    options: LibraryReadOptions,
  ): number {
    const byId = left.movie_id - right.movie_id;
    if (options.sort === "title") {
      return left.title.localeCompare(right.title) || byId;
    }
    if (options.sort === "rating") {
      const stars = (right.state.rating ?? -1) - (left.state.rating ?? -1);
      const watched = sortKeyDate(right.state, options.tab).localeCompare(
        sortKeyDate(left.state, options.tab),
      );
      return stars || watched || byId;
    }
    if (options.sort === "release") {
      return (right.release_year ?? -1) - (left.release_year ?? -1) || byId;
    }
    if (options.sort === "tmdb") {
      return (right.tmdb_rating ?? -1) - (left.tmdb_rating ?? -1) || byId;
    }
    return (
      sortKeyDate(right.state, options.tab).localeCompare(
        sortKeyDate(left.state, options.tab),
      ) || byId
    );
  }

  function page(options: LibraryReadOptions, offset: number): LibraryResponse {
    const limit = options.limit ?? 12;
    const needle = options.query.trim().toLowerCase();
    const genre = options.genre ?? null;
    const yearFrom = options.yearFrom ?? null;
    const yearTo = options.yearTo ?? null;

    const matching = emptyTabs.has(options.tab)
      ? []
      : [...movies.values()]
          .filter((movie) => movieMatchesTab(movie.state, options.tab))
          .filter((movie) => !needle || movie.title.toLowerCase().includes(needle))
          .filter((movie) => !genre || movie.genres.includes(genre))
          // A year bound is the one filter that can hide a row the tab holds:
          // a title the snapshot has never covered carries no year and drops
          // out the moment either bound is set.
          .filter((movie) => yearFrom === null || (movie.release_year ?? -1) >= yearFrom)
          .filter((movie) => yearTo === null || (movie.release_year ?? -1) <= yearTo)
          .sort((left, right) => compare(left, right, options));

    const window = matching.slice(offset, offset + limit);
    const hasMore = offset + limit < matching.length;
    return {
      counts: counts(),
      genre,
      items: window.map((movie) => ({ ...movie, state: { ...movie.state } })),
      page: {
        has_more: hasMore,
        matched: matching.length,
        next_cursor: hasMore ? recordedLibraryCursor(options, offset + limit) : null,
      },
      query: options.query || null,
      sort: options.sort,
      tab: options.tab,
      tenant_id: TENANT_ID,
      user_id: options.userId,
      year_from: yearFrom,
      year_to: yearTo,
    };
  }

  return {
    readLibrary(options) {
      if (failing.has("library")) {
        return Promise.resolve(recordedFailure("library"));
      }
      const offset = cursorOffset(options);
      if (offset === null) {
        return Promise.resolve(
          failureState({
            detail: "library cursor is invalid for this query",
            httpStatus: 400,
            reason: "bad-request",
            requestId: FIXTURE_REQUEST_ID,
            resource: "library",
            status: "upstream-error",
          }),
        );
      }
      const response = page(options, offset);
      return Promise.resolve(
        response.items.length
          ? readyState("library", response, FIXTURE_REQUEST_ID, RECORDED)
          : emptyState("library", response, FIXTURE_REQUEST_ID, RECORDED),
      );
    },

    readTasteProfile() {
      if (failing.has("taste-profile")) {
        return Promise.resolve(recordedFailure("taste-profile"));
      }
      return Promise.resolve(
        summary.rating_count
          ? readyState("taste-profile", summary, FIXTURE_REQUEST_ID, RECORDED)
          : emptyState("taste-profile", summary, FIXTURE_REQUEST_ID, RECORDED),
      );
    },

    readState(_userId, movieId) {
      return Promise.resolve(movies.get(movieId)?.state ?? null);
    },

    readMovieDetail(userId, movieId) {
      const movie = movies.get(movieId);
      if (!movie) {
        return Promise.resolve(
          failureState({
            status: "not-found",
            resource: "movie-detail",
            reason: "not-found",
            requestId: FIXTURE_REQUEST_ID,
          }),
        );
      }
      const response: MovieDetailResponse = {
        item: {
          details: SEEN_DETAILS[movieId] ?? null,
          genres: [...movie.genres],
          interaction_count: 0,
          metadata_source: movie.poster_url ? "reviewed-fixture" : "movielens",
          movie_id: movieId,
          overview: null,
          poster_url: movie.poster_url,
          release_year: movie.release_year,
          source_status: movie.poster_url ? "complete" : "unavailable",
          state: { ...movie.state },
          title: movie.title,
          tmdb_id: null,
        },
        tenant_id: TENANT_ID,
        user_id: userId,
      };
      return Promise.resolve(
        readyState("movie-detail", response, FIXTURE_REQUEST_ID, RECORDED),
      );
    },

    mutate(request) {
      const movie = movies.get(request.movieId);
      if (!movie) {
        return Promise.resolve({
          status: "failed" as const,
          failure: recordedFailure("library"),
        });
      }
      const next = applyActionToState(
        movie.state,
        movieStateActionOf(request),
        new Date().toISOString(),
      );
      const committed = { ...next, revision: movie.state.revision + 1 };
      movies.set(request.movieId, { ...movie, state: committed });

      const rated = [...movies.values()].filter((item) => item.state.rating !== null);
      summary = {
        ...summary,
        average_rating: rated.length
          ? rated.reduce((total, item) => total + (item.state.rating ?? 0), 0) / rated.length
          : null,
        rating_count: rated.length,
      };

      return Promise.resolve({
        status: "committed" as const,
        state: committed,
        outcome: "changed" as const,
        requestId: FIXTURE_REQUEST_ID,
        replayed: false,
      });
    },
  };
}

/** The recorded first page a preview route renders before any interaction. */
export function recordedLibraryState(
  options: LibraryReadOptions & RecordedLibraryOptions,
): Promise<ResourceState<LibraryResponse>> {
  return createRecordedLibraryClient(options).readLibrary(options);
}
