import type { components } from "./api.generated";

export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type RecommendationResponse = components["schemas"]["RecommendationResponse"];
export type HistoryItem = components["schemas"]["HistoryItem"];
export type HistoryResponse = components["schemas"]["HistoryResponse"];
export type CatalogItem = components["schemas"]["CatalogItem"];
export type CatalogResponse = components["schemas"]["CatalogResponse"];
export type CatalogPageInfo = components["schemas"]["CatalogPageInfo"];
export type MovieDetailResponse = components["schemas"]["MovieDetailResponse"];
export type PersonaItem = components["schemas"]["PersonaItem"];
export type PersonaResponse = components["schemas"]["PersonaResponse"];
export type CurrentActorResponse = components["schemas"]["CurrentActorResponse"];
export type MovieState = components["schemas"]["MovieStateResponse"];
export type FeedbackMutationResponse =
  components["schemas"]["FeedbackMutationResponse"];
export type LibraryMovie = components["schemas"]["LibraryMovieResponse"];
export type LibraryResponse = components["schemas"]["LibraryResponse"];
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

export interface UserDashboard {
  recommendations: RecommendationResponse;
  history: HistoryResponse;
  catalog: CatalogResponse;
}
