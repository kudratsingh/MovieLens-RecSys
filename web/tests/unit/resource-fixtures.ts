import type {
  CatalogResponse,
  LibraryResponse,
  MovieDetailResponse,
  MovieState,
  OnlineUserFeatures,
  RecommendationAuditResponse,
  RecommendationResponse,
  ServingPolicy,
  TasteSummaryResponse,
} from "@/lib/api";

/**
 * Contract-shaped payloads for boundary tests. These are test inputs only —
 * production reads cannot reach them, which `resources-fixture-lockout.test.ts`
 * asserts structurally.
 */

export const movieState: MovieState = {
  dismissed_at: null,
  movie_id: 101,
  rating: 4.5,
  rating_updated_at: "2026-08-01T10:00:00Z",
  revision: 3,
  tenant_id: "demo",
  updated_at: "2026-08-01T10:00:00Z",
  user_id: 900000101,
  watched_at: "2026-08-01T09:00:00Z",
  watchlisted_at: null,
};

/**
 * The serving policy the API reports alongside every recommendation. Both
 * variants are recorded because the truthful copy differs: below the router's
 * reported threshold of positive watched signals the response is fallback, and
 * only a `learned: true` policy licenses learned-serving language.
 */
export const learnedServingPolicy: ServingPolicy = {
  excluded_count: 9,
  filter_policy: "watched-and-dismissed-excluded-v1",
  learned: true,
  name: "item-item-lightgbm",
  positive_signal_count: 12,
  reason:
    "learned-two-stage: item-item-cosine retrieval over 12 positive seeds, " +
    "ranked by lgbm-ranker-2026.08",
  // An uncalibrated LambdaRank ordering — never a probability or a match percentage.
  score_scale: "lightgbm-rank-score",
  threshold: 10,
};

export const fallbackServingPolicy: ServingPolicy = {
  excluded_count: 3,
  filter_policy: "watched-and-dismissed-excluded-v1",
  learned: false,
  name: "popularity",
  positive_signal_count: 3,
  reason: "cold-start: 3 positive watched signals below threshold 10",
  score_scale: "tenant-interaction-count",
  threshold: 10,
};

export const recommendationResponse: RecommendationResponse = {
  items: [
    {
      genres: ["Thriller", "Drama"],
      metadata_source: "reviewed-fixture",
      movie_id: 101,
      overview: "A con artist enters a secluded estate.",
      poster_url: "/posters/handmaiden.svg",
      reason: "Similar to movies in this persona's watched history.",
      release_year: 2016,
      score: 0.82,
      state: null,
      title: "The Handmaiden",
      tmdb_id: "290098",
    },
  ],
  model_version: "lgbm-ranker-2026.08",
  policy: "item-item-lightgbm",
  serving_policy: learnedServingPolicy,
  tenant_id: "demo",
  user_id: 900000101,
};

export const emptyRecommendationResponse: RecommendationResponse = {
  ...recommendationResponse,
  items: [],
};

/** A persona still below the signal threshold: popularity, not learned. */
export const fallbackRecommendationResponse: RecommendationResponse = {
  items: [
    {
      genres: ["Drama"],
      metadata_source: "movielens",
      movie_id: 109,
      overview: null,
      poster_url: null,
      reason: "Popular with viewers in this tenant",
      release_year: 2011,
      score: 42,
      state: null,
      title: "A Separation",
      tmdb_id: null,
    },
  ],
  model_version: "popularity-v1",
  policy: "popularity",
  serving_policy: fallbackServingPolicy,
  tenant_id: "demo",
  user_id: 900000104,
};

export const catalogResponse: CatalogResponse = {
  items: [
    {
      genres: ["Drama"],
      interaction_count: 42,
      metadata_source: "movielens",
      movie_id: 109,
      overview: null,
      poster_url: null,
      release_year: 2011,
      source_status: "partial",
      state: movieState,
      title: "A Separation",
      tmdb_id: null,
    },
  ],
  page: { has_more: true, next_cursor: "opaque-cursor" },
  tenant_id: "demo",
  user_id: 900000101,
};

export const movieDetailResponse: MovieDetailResponse = {
  // Detail's item type carries `details`; the list items above deliberately do
  // not. A `null` here is the common case in the reviewed snapshot — most
  // titles have no enriched TMDB record — and it is the value the degraded
  // detail layout is built around.
  item: { ...catalogResponse.items[0], details: null },
  tenant_id: "demo",
  user_id: 900000101,
};

export const libraryResponse: LibraryResponse = {
  counts: { history: 6, rated: 4, watchlist: 2 },
  genre: null,
  items: [
    {
      genres: ["Crime", "Mystery"],
      movie_id: 103,
      poster_url: "https://image.tmdb.org/t/p/w500/memories.jpg",
      release_year: 2003,
      state: movieState,
      title: "Memories of Murder",
      tmdb_rating: 8.1,
    },
  ],
  page: { has_more: false, matched: 1, next_cursor: null },
  query: null,
  sort: "recent",
  tab: "rated",
  tenant_id: "demo",
  user_id: 900000101,
  year_from: null,
  year_to: null,
};

export const tasteSummaryResponse: TasteSummaryResponse = {
  average_rating: 4.1,
  explanation: "Summarizes the ratings currently stored for this persona.",
  generated_at: "2026-08-21T12:00:00Z",
  rating_count: 4,
  source: "live-ratings-v1",
  tenant_id: "demo",
  top_genres: [{ average_rating: 4.5, genre: "Drama", rated_count: 3 }],
  user_id: 900000101,
};

export const auditResponse: RecommendationAuditResponse = {
  items: [
    {
      actor_user_id: "demo-actor",
      candidate_latency_ms: 6.2,
      candidate_version: "item-item-v3",
      created_at: "2026-08-21T12:00:00Z",
      endpoint: "/users/900000101/recommendations",
      candidate_sources: { "item-item-cosine": 96, "popularity-fill": 4 },
      correlation_id: "bff-discover-0f9d1c22",
      excluded_count: 9,
      exclusion_hash: "7d1f1a0c9b3e4a52",
      fallback_reason: null,
      feature_event_time: "2026-08-21T06:00:00Z",
      feature_latency_ms: 3.1,
      feature_version: "online-features-v2",
      filter_policy: "watched-and-dismissed-excluded-v1",
      http_status: 200,
      input_state_hash: "3c4a9f20b7e15d88",
      input_state_revision: 12,
      latency_ms: 41.9,
      model_latency_ms: 18.4,
      model_version: "lgbm-ranker-2026.08",
      outcome: "served",
      policy: "item-item-lightgbm",
      positive_signal_count: 12,
      predictions: [
        {
          candidate_source: "item-item-cosine",
          features: { user_interaction_count: 12 },
          movie_id: 101,
          score: 0.82,
          // The most recently watched title this candidate was retrieved from.
          seed_movie_id: 47,
        },
      ],
      ranker_latency_ms: 12.7,
      ranker_version: "lgbm-ranker-2026.08",
      reason:
        "learned-two-stage: item-item-cosine retrieval over 8 positive seeds, " +
        "ranked by lgbm-ranker-2026.08",
      request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
      tenant_id: "demo",
      user_id: 900000101,
    },
  ],
  tenant_id: "demo",
  user_id: 900000101,
};

export const onlineFeatures: OnlineUserFeatures = {
  feature_timestamp: "2026-08-21T06:00:00Z",
  source: "feast-redis",
  tenant_id: "demo",
  user_days_active: 120,
  user_days_since_last_interaction: 2,
  user_id: 900000101,
  user_interaction_count: 12,
};
