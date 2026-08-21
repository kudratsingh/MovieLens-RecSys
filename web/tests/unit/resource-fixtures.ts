import type {
  CatalogResponse,
  LibraryResponse,
  MovieDetailResponse,
  MovieState,
  OnlineUserFeatures,
  RecommendationAuditResponse,
  RecommendationResponse,
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
      title: "The Handmaiden",
      tmdb_id: "290098",
    },
  ],
  model_version: "lgbm-ranker-2026.08",
  policy: "item-item-lightgbm",
  tenant_id: "demo",
  user_id: 900000101,
};

export const emptyRecommendationResponse: RecommendationResponse = {
  ...recommendationResponse,
  items: [],
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
  item: catalogResponse.items[0],
  tenant_id: "demo",
  user_id: 900000101,
};

export const libraryResponse: LibraryResponse = {
  counts: { history: 6, rated: 4, watchlist: 2 },
  items: [
    {
      genres: ["Crime", "Mystery"],
      movie_id: 103,
      state: movieState,
      title: "Memories of Murder",
    },
  ],
  page: { has_more: false, next_cursor: null },
  query: null,
  sort: "recent",
  tab: "rated",
  tenant_id: "demo",
  user_id: 900000101,
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
      fallback_reason: null,
      feature_latency_ms: 3.1,
      feature_version: "online-features-v2",
      http_status: 200,
      latency_ms: 41.9,
      model_latency_ms: 18.4,
      model_version: "lgbm-ranker-2026.08",
      outcome: "served",
      policy: "item-item-lightgbm",
      predictions: [{ features: { user_interaction_count: 12 }, movie_id: 101, score: 0.82 }],
      ranker_latency_ms: 12.7,
      ranker_version: "lgbm-ranker-2026.08",
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
