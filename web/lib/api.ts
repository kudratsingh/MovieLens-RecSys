import type { components } from "./api.generated";

export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type RecommendationResponse = components["schemas"]["RecommendationResponse"];
export type ServingPolicy = components["schemas"]["ServingPolicyResponse"];
export type HistoryItem = components["schemas"]["HistoryItem"];
export type HistoryResponse = components["schemas"]["HistoryResponse"];
export type CatalogItem = components["schemas"]["CatalogItem"];
export type CatalogResponse = components["schemas"]["CatalogResponse"];
export type CatalogPageInfo = components["schemas"]["CatalogPageInfo"];
export type MovieDetailResponse = components["schemas"]["MovieDetailResponse"];
/**
 * Detail returns its own item type: a `CatalogItem` plus the enriched TMDB
 * block. The split is the API's, and it is the reason a Browse card cannot
 * start reading a field the list response does not carry.
 */
export type MovieDetailItem = components["schemas"]["MovieDetailItem"];
export type MovieDetails = components["schemas"]["MovieDetails"];
export type MovieCastMember = components["schemas"]["MovieCastMember"];
export type MovieTrailer = components["schemas"]["MovieTrailer"];
export type TmdbRating = components["schemas"]["TmdbRating"];
export type PersonaItem = components["schemas"]["PersonaItem"];
export type PersonaResponse = components["schemas"]["PersonaResponse"];
export type CurrentActorResponse = components["schemas"]["CurrentActorResponse"];
export type MovieState = components["schemas"]["MovieStateResponse"];
export type FeedbackMutationResponse =
  components["schemas"]["FeedbackMutationResponse"];
export type LibraryResponse = components["schemas"]["LibraryResponse"];
export type LibraryMovie = components["schemas"]["LibraryMovieResponse"];
/** The ordering a Library page was built under, echoed back by the endpoint. */
export type LibrarySortValue = LibraryResponse["sort"];
/**
 * The Library page block. `CursorPageResponse` is the Library's alone — the
 * catalog carries its own `CatalogPageInfo`, which is what keeps `matched` off
 * a response the catalog contract forbids to invent a total for.
 */
export type LibraryPage = components["schemas"]["CursorPageResponse"];

export type LibraryCounts = components["schemas"]["LibraryCountsResponse"];
export type CursorPage = components["schemas"]["CursorPageResponse"];
export type TasteSummaryResponse = components["schemas"]["TasteSummaryResponse"];
export type TasteGenre = components["schemas"]["TasteGenreResponse"];
export type AuditPredictionItem = components["schemas"]["AuditPredictionItem"];
export type RecommendationAuditItem =
  components["schemas"]["RecommendationAuditItem"];
export type RecommendationAuditResponse =
  components["schemas"]["RecommendationAuditResponse"];
export type OnlineUserFeatures =
  components["schemas"]["OnlineUserFeaturesResponse"];
export type UserPreferences = components["schemas"]["UserPreferencesResponse"];
export type UserPreferencesMutation =
  components["schemas"]["UserPreferencesMutationResponse"];

export interface UserDashboard {
  recommendations: RecommendationResponse;
  history: HistoryResponse;
  catalog: CatalogResponse;
}
