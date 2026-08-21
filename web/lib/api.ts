import type { components } from "./api.generated";

export type RecommendationItem = components["schemas"]["RecommendationItem"];
export type RecommendationResponse = components["schemas"]["RecommendationResponse"];
export type HistoryItem = components["schemas"]["HistoryItem"];
export type HistoryResponse = components["schemas"]["HistoryResponse"];
export type CatalogItem = components["schemas"]["CatalogItem"];
export type CatalogResponse = components["schemas"]["CatalogResponse"];
export type PersonaItem = components["schemas"]["PersonaItem"];
export type PersonaResponse = components["schemas"]["PersonaResponse"];
export type CurrentActorResponse = components["schemas"]["CurrentActorResponse"];

export interface UserDashboard {
  recommendations: RecommendationResponse;
  history: HistoryResponse;
  catalog: CatalogResponse;
}
