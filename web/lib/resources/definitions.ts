/**
 * The registry of live product resources.
 *
 * One entry per region a route can load on its own. The timeout is part of the
 * contract rather than a call-site detail: a region that blocks the first
 * movie gets a short budget, and progressive-disclosure evidence gets a longer
 * one because nothing is waiting on it.
 */

import type {
  CatalogResponse,
  HistoryResponse,
  LibraryResponse,
  MovieDetailResponse,
  OnlineUserFeatures,
  RecommendationAuditResponse,
  RecommendationResponse,
  TasteSummaryResponse,
} from "@/lib/api";
import type { LiveResourceName } from "@/lib/resources/state";
import {
  isCatalogResponse,
  isHistoryResponse,
  isLibraryResponse,
  isMovieDetailResponse,
  isOnlineUserFeatures,
  isRecommendationAuditResponse,
  isRecommendationResponse,
  isTasteSummaryResponse,
} from "@/lib/resources/validate";

export type ResourceDefinition<T> = {
  name: LiveResourceName;
  /** Sentence-case label used in region copy: "Recommendations could not…". */
  label: string;
  timeoutMs: number;
  guard: (value: unknown) => value is T;
  /** A 200 that carries no rows is `empty`, not `ready` with nothing to show. */
  isEmpty?: (data: T) => boolean;
};

const noItems = (data: { items: readonly unknown[] }) => data.items.length === 0;

export const RECOMMENDATIONS: ResourceDefinition<RecommendationResponse> = {
  name: "recommendations",
  label: "Recommendations",
  timeoutMs: 3_000,
  guard: isRecommendationResponse,
  isEmpty: noItems,
};

export const HISTORY: ResourceDefinition<HistoryResponse> = {
  name: "history",
  label: "Watch history",
  timeoutMs: 3_000,
  guard: isHistoryResponse,
  isEmpty: noItems,
};

export const CATALOG: ResourceDefinition<CatalogResponse> = {
  name: "catalog",
  label: "Catalog",
  timeoutMs: 5_000,
  guard: isCatalogResponse,
  isEmpty: noItems,
};

export const MOVIE_DETAIL: ResourceDefinition<MovieDetailResponse> = {
  name: "movie-detail",
  label: "Movie detail",
  timeoutMs: 4_000,
  guard: isMovieDetailResponse,
};

export const LIBRARY: ResourceDefinition<LibraryResponse> = {
  name: "library",
  label: "Library",
  timeoutMs: 5_000,
  guard: isLibraryResponse,
  isEmpty: noItems,
};

export const TASTE_PROFILE: ResourceDefinition<TasteSummaryResponse> = {
  name: "taste-profile",
  label: "Taste summary",
  timeoutMs: 3_000,
  guard: isTasteSummaryResponse,
  isEmpty: (data) => data.rating_count === 0,
};

export const AUDITS: ResourceDefinition<RecommendationAuditResponse> = {
  name: "audits",
  label: "Prediction audits",
  timeoutMs: 4_000,
  guard: isRecommendationAuditResponse,
  isEmpty: noItems,
};

export const FEATURES: ResourceDefinition<OnlineUserFeatures> = {
  name: "features",
  label: "Online features",
  timeoutMs: 4_000,
  guard: isOnlineUserFeatures,
};

export const RESOURCE_LABELS: Record<LiveResourceName, string> = {
  recommendations: RECOMMENDATIONS.label,
  history: HISTORY.label,
  catalog: CATALOG.label,
  "movie-detail": MOVIE_DETAIL.label,
  library: LIBRARY.label,
  "taste-profile": TASTE_PROFILE.label,
  audits: AUDITS.label,
  features: FEATURES.label,
};
