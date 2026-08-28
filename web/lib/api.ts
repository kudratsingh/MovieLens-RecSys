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
/**
 * The Library response, widened locally ahead of the generated contract.
 *
 * `lib/api.generated.ts` is produced from `docs/api/openapi.json`, which the
 * backend regenerates — so the Seen work arrives here first. The endpoint gains
 * two sort values, three echoed filters, an exact `page.matched`, and a per-row
 * `tmdb_rating`; every one of them is optional here on the same reasoning
 * `movieMetaLine` already applies to `release_year`. The API and the web app
 * deploy as separate images and either can be the older one, so a field that
 * has not shipped yet must read as absent rather than as a broken page.
 *
 * When the generated types carry these, this block collapses back to the plain
 * `components["schemas"]` aliases it replaced.
 */
export type LibrarySortValue =
  | components["schemas"]["LibraryResponse"]["sort"]
  | "release"
  | "tmdb";

export type LibraryMovie = components["schemas"]["LibraryMovieResponse"] & {
  /** The TMDB crowd average. The vote count stays on the detail record. */
  tmdb_rating?: number | null;
};

export type LibraryPage = components["schemas"]["CursorPageResponse"] & {
  /** Rows matching the tab and the filters, ignoring cursor and limit. */
  matched?: number;
};

export type LibraryResponse = Omit<
  components["schemas"]["LibraryResponse"],
  "items" | "page" | "sort"
> & {
  items: LibraryMovie[];
  page: LibraryPage;
  sort: LibrarySortValue;
  genre?: string | null;
  year_from?: number | null;
  year_to?: number | null;
};

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
