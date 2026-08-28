/**
 * Recorded Discover payloads in the *live* API shape.
 *
 * Bundle 4's `movie-fixtures.ts` records presentational cards; these record the
 * FastAPI responses themselves, so the recorded screenshot harness and the
 * component tests exercise the same translation, validation, and truthfulness
 * code the live route runs. A fixture that skipped that translation would let
 * a mapping bug ship with green screenshots.
 *
 * These are test inputs. They only ever reach a page through
 * `lib/resources/fixture-gate.ts`, which throws outside the isolated UI mode
 * and always throws in production.
 */

import { FIXTURE_REQUEST_ID } from "@/lib/resources/fixture-gate";
import type {
  HistoryResponse,
  MovieState,
  OnlineUserFeatures,
  RecommendationAuditResponse,
  RecommendationItem,
  RecommendationResponse,
  ServingPolicy,
  UserPreferences,
} from "@/lib/api";

export const FIXTURE_TENANT_ID = "demo";
export const FIXTURE_USER_ID = 900000101;

type ItemSeed = {
  movie_id: number;
  title: string;
  release_year: number;
  genres: string[];
  poster: string | null;
  overview: string;
  score: number;
};

const LEARNED_REASON = "LightGBM rank over learned item-item candidates";
const FALLBACK_REASON = "Popular with viewers in this tenant";

const FILTER_POLICY = "watched-and-dismissed-excluded-v1";

/** Mirrors the shape `tests/unit/resource-fixtures.ts` records for the API. */
const learnedPolicy: ServingPolicy = {
  excluded_count: 9,
  filter_policy: FILTER_POLICY,
  learned: true,
  name: "item-item-lightgbm",
  positive_signal_count: 8,
  reason:
    "learned-two-stage: item-item-cosine retrieval over 8 positive seeds, " +
    "ranked by lgbm-ranker-2026.08",
  // An uncalibrated LambdaRank ordering — never a probability or a percentage.
  score_scale: "lightgbm-rank-score",
  threshold: 5,
};

const fallbackPolicy: ServingPolicy = {
  excluded_count: 3,
  filter_policy: FILTER_POLICY,
  learned: false,
  name: "popularity",
  positive_signal_count: 3,
  reason: "cold-start: 3 positive watched signals below threshold 5",
  score_scale: "tenant-interaction-count",
  threshold: 5,
};

const SEEDS: readonly ItemSeed[] = [
  {
    movie_id: 101,
    title: "The Handmaiden (2016)",
    release_year: 2016,
    genres: ["Thriller", "Drama"],
    poster: "/posters/handmaiden.svg",
    overview:
      "A con artist enters a secluded estate and discovers that every plan has another plan inside it.",
    score: 4.8213,
  },
  {
    movie_id: 102,
    title: "In the Mood for Love (2000)",
    release_year: 2000,
    genres: ["Romance", "Drama"],
    poster: "/posters/in-the-mood.svg",
    overview: "Two neighbors form a quiet bond after making the same discovery.",
    score: 4.4471,
  },
  {
    movie_id: 103,
    title: "Memories of Murder (2003)",
    release_year: 2003,
    genres: ["Crime", "Mystery"],
    poster: "/posters/memories.svg",
    overview: "Detectives chase a pattern through a rain-soaked rural province.",
    score: 4.1902,
  },
  {
    movie_id: 104,
    title: "Portrait of a Lady on Fire (2019)",
    release_year: 2019,
    genres: ["Drama", "Romance"],
    poster: "/posters/portrait.svg",
    overview: "A painter and her subject see each other with uncommon clarity.",
    score: 3.9755,
  },
  {
    movie_id: 105,
    title: "Perfect Blue (1997)",
    release_year: 1997,
    genres: ["Animation", "Thriller"],
    poster: "/posters/perfect-blue.svg",
    overview: "A performer loses her footing between image, memory, and reality.",
    score: 3.7318,
  },
  {
    movie_id: 106,
    title: "Moonlight (2016)",
    release_year: 2016,
    genres: ["Drama"],
    poster: "/posters/moonlight.svg",
    overview: "Three chapters trace a young man becoming himself.",
    score: 3.5006,
  },
  {
    movie_id: 107,
    title: "The Worst Person in the World (2021)",
    release_year: 2021,
    genres: ["Comedy", "Drama"],
    poster: "/posters/worst-person.svg",
    overview: "A restless search for a life that feels chosen rather than inherited.",
    score: 3.2884,
  },
  {
    movie_id: 108,
    title: "Burning (2018)",
    release_year: 2018,
    genres: ["Mystery", "Drama"],
    poster: "/posters/burning.svg",
    overview:
      "An old acquaintance, a new stranger, and a disappearance refuse to line up.",
    score: 3.0417,
  },
  {
    // Deliberately without artwork so the deterministic fallback is recorded.
    movie_id: 109,
    title: "A Separation (2011)",
    release_year: 2011,
    genres: ["Drama"],
    poster: null,
    overview: "One family decision opens a knot of obligation and truth.",
    score: 2.9130,
  },
  {
    movie_id: 110,
    title: "Decision to Leave (2022)",
    release_year: 2022,
    genres: ["Mystery", "Romance"],
    poster: "/posters/decision.svg",
    overview: "A detective finds suspicion and longing difficult to separate.",
    score: 2.7746,
  },
];

function item(seed: ItemSeed, reason: string): RecommendationItem {
  return {
    movie_id: seed.movie_id,
    title: seed.title,
    genres: seed.genres,
    tmdb_id: null,
    score: seed.score,
    reason,
    poster_url: seed.poster,
    overview: seed.overview,
    release_year: seed.release_year,
    metadata_source: "reviewed-fixture",
    // A ranked title is never watched or dismissed — serving excludes both
    // before ranking — so the recorded queue carries no prior state.
    state: null,
  };
}

function response(
  policy: ServingPolicy,
  modelVersion: string,
  items: RecommendationItem[],
): RecommendationResponse {
  return {
    tenant_id: FIXTURE_TENANT_ID,
    user_id: FIXTURE_USER_ID,
    model_version: modelVersion,
    // The flat string stays the envelope's name, exactly as FastAPI reports it.
    policy: policy.name,
    serving_policy: policy,
    items,
  };
}

export const learnedRecommendations: RecommendationResponse = response(
  learnedPolicy,
  "item-item-v3/lgbm-ranker-2026.08",
  SEEDS.map((seed) => item(seed, LEARNED_REASON)),
);

export const fallbackRecommendations: RecommendationResponse = response(
  fallbackPolicy,
  "popularity-v1",
  SEEDS.map((seed) => item(seed, FALLBACK_REASON)),
);

export const emptyRecommendations: RecommendationResponse = response(
  { ...fallbackPolicy, excluded_count: 0 },
  "popularity-v1",
  [],
);

/**
 * Same list, but the first poster points at a path that does not resolve, so
 * the recorded matrix can show artwork failing without losing the decision.
 */
export const posterFailureRecommendations: RecommendationResponse = response(
  learnedPolicy,
  "item-item-v3/lgbm-ranker-2026.08",
  SEEDS.map((seed, index) =>
    item(
      index === 0 ? { ...seed, poster: "/posters/missing-artwork.svg" } : seed,
      LEARNED_REASON,
    ),
  ),
);

/**
 * A watchlist entry the API has already committed, recorded in the shape a
 * recommendation response carries it in. `revision` is 2 rather than 1 on
 * purpose: a real watchlist entry is rarely a persona's first write, and a
 * fixture that always said 1 would let a client that assumes revision 0 or 1
 * pass the recorded matrix and fail on the stack.
 */
function watchlistedState(movieId: number): MovieState {
  return {
    tenant_id: FIXTURE_TENANT_ID,
    user_id: FIXTURE_USER_ID,
    movie_id: movieId,
    watched_at: null,
    rating: null,
    rating_updated_at: null,
    watchlisted_at: "2026-08-27T19:12:00+00:00",
    dismissed_at: null,
    revision: 2,
    updated_at: "2026-08-27T19:12:00+00:00",
  };
}

/**
 * The first four titles are already on the persona's watchlist.
 *
 * Four rather than one because the states the harness has to reach are a
 * sequence: skip, skip, skip — which is what earns the one-time nudge — and
 * then a fourth watchlisted title still standing so the preference has
 * something visible to hold back afterwards.
 */
export const WATCHLISTED_MOVIE_IDS: readonly number[] = [101, 102, 103, 104];

export const watchlistedRecommendations: RecommendationResponse = response(
  learnedPolicy,
  "item-item-v3/lgbm-ranker-2026.08",
  SEEDS.map((seed) => {
    const base = item(seed, LEARNED_REASON);
    return WATCHLISTED_MOVIE_IDS.includes(seed.movie_id)
      ? { ...base, state: watchlistedState(seed.movie_id) }
      : base;
  }),
);

/** The untouched default: watchlisted titles may take the featured slot. */
export const featuredPreferencesOn: UserPreferences = {
  tenant_id: FIXTURE_TENANT_ID,
  user_id: FIXTURE_USER_ID,
  feature_watchlisted_titles: true,
  revision: 0,
  updated_at: null,
};

/** The answered state, at the revision a real first write would leave. */
export const featuredPreferencesOff: UserPreferences = {
  tenant_id: FIXTURE_TENANT_ID,
  user_id: FIXTURE_USER_ID,
  feature_watchlisted_titles: false,
  revision: 1,
  updated_at: "2026-08-27T19:20:00+00:00",
};

export const discoverHistory: HistoryResponse = {
  tenant_id: FIXTURE_TENANT_ID,
  user_id: FIXTURE_USER_ID,
  items: [
    {
      movie_id: 6,
      title: "Heat (1995)",
      release_year: 1995,
      genres: ["Action", "Crime", "Thriller"],
      poster_url: "/posters/burning.svg",
      rating: 4.5,
      timestamp: 1_767_312_000,
    },
    {
      movie_id: 2571,
      title: "Matrix, The (1999)",
      release_year: 1999,
      genres: ["Action", "Sci-Fi", "Thriller"],
      poster_url: "/posters/perfect-blue.svg",
      rating: 5,
      timestamp: 1_767_225_600,
    },
    {
      movie_id: 2028,
      title: "Saving Private Ryan (1998)",
      release_year: 1998,
      genres: ["Action", "Drama", "War"],
      // Deliberately without artwork, so the recorded matrix carries a history
      // row falling back to the shared mark next to rows that have a poster.
      poster_url: null,
      rating: null,
      timestamp: 1_767_139_200,
    },
    {
      movie_id: 110,
      title: "Braveheart (1995)",
      release_year: 1995,
      genres: ["Action", "Drama", "War"],
      poster_url: "/posters/moonlight.svg",
      rating: 3.5,
      timestamp: 1_767_052_800,
    },
  ],
};

export const emptyHistory: HistoryResponse = {
  tenant_id: FIXTURE_TENANT_ID,
  user_id: FIXTURE_USER_ID,
  items: [],
};

export const discoverAudits: RecommendationAuditResponse = {
  tenant_id: FIXTURE_TENANT_ID,
  user_id: FIXTURE_USER_ID,
  items: [
    {
      request_id: "6f1b4c02-9f3d-4a01-8a6c-31c0f9a2b7d4",
      correlation_id: FIXTURE_REQUEST_ID,
      tenant_id: FIXTURE_TENANT_ID,
      user_id: FIXTURE_USER_ID,
      actor_user_id: "demo-walkthrough",
      endpoint: "/users/900000101/recommendations",
      outcome: "success",
      http_status: 200,
      policy: "item-item-lightgbm",
      model_version: "item-item-v3/lgbm-ranker-2026.08",
      candidate_version: "item-item-v3",
      ranker_version: "lgbm-ranker-2026.08",
      feature_version: "online-features-v2",
      fallback_reason: null,
      reason: learnedPolicy.reason,
      positive_signal_count: 8,
      excluded_count: 9,
      filter_policy: FILTER_POLICY,
      input_state_revision: 12,
      input_state_hash: "sha256:9f2c41d0",
      exclusion_hash: "sha256:3ab77e15",
      feature_event_time: "2026-08-21T06:00:00.000Z",
      candidate_sources: { "item-item-cosine": 500 },
      latency_ms: 41.6,
      candidate_latency_ms: 11.2,
      feature_latency_ms: 6.4,
      ranker_latency_ms: 18.9,
      model_latency_ms: 37.1,
      created_at: "2026-08-21T09:14:02.418Z",
      predictions: [
        {
          movie_id: 101,
          score: 4.8213,
          candidate_source: "item-item-cosine",
          features: {
            item_interaction_count_30d: 812,
            user_days_active: 96,
            user_interaction_count: 8,
          },
        },
        {
          movie_id: 102,
          score: 4.4471,
          candidate_source: "item-item-cosine",
          features: {
            item_interaction_count_30d: 604,
            user_days_active: 96,
            user_interaction_count: 8,
          },
        },
      ],
    },
  ],
};

/** The same audit shape a popularity response produces: no ranker, a reason. */
export const fallbackAudits: RecommendationAuditResponse = {
  ...discoverAudits,
  items: discoverAudits.items.map((audit) => ({
    ...audit,
    policy: "popularity",
    model_version: "popularity-v1",
    candidate_version: "popularity-v1",
    ranker_version: "not-run",
    feature_version: "not-read",
    fallback_reason: "cold-start",
    reason: fallbackPolicy.reason,
    positive_signal_count: 3,
    excluded_count: 3,
    candidate_sources: { popularity: 50 },
    feature_event_time: null,
    ranker_latency_ms: 0,
    feature_latency_ms: 0,
    model_latency_ms: 0,
    predictions: audit.predictions.map((prediction) => ({
      ...prediction,
      candidate_source: "popularity",
      features: {},
    })),
  })),
};

export const discoverFeatures: OnlineUserFeatures = {
  tenant_id: FIXTURE_TENANT_ID,
  user_id: FIXTURE_USER_ID,
  source: "feast-online-redis",
  feature_timestamp: "2026-08-21T06:00:00.000Z",
  user_interaction_count: 8,
  user_days_active: 96,
  user_days_since_last_interaction: 2,
};
