/**
 * Narrow runtime guards for the JSON crossing the FastAPI → BFF → browser
 * boundary.
 *
 * The generated OpenAPI types describe what the API promises; these guards
 * check what actually arrived. They are deliberately hand-written and narrow:
 * a schema library would be a new production dependency for the frontend, and
 * the interesting failure here is a contract drift or a proxy returning an
 * error page, not deep structural validation. Each guard checks the fields a
 * route genuinely reads, and a failure becomes `upstream-error`, never a
 * silently half-rendered region.
 */

import type {
  CatalogItem,
  CatalogResponse,
  FeedbackMutationResponse,
  HistoryResponse,
  LibraryResponse,
  MovieDetailItem,
  MovieDetailResponse,
  MovieState,
  OnlineUserFeatures,
  RecommendationAuditResponse,
  RecommendationResponse,
  TasteSummaryResponse,
} from "@/lib/api";

type Guard<T> = (value: unknown) => value is T;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === "boolean";
}

function nullable<T>(guard: Guard<T>): Guard<T | null> {
  return (value): value is T | null => value === null || guard(value);
}

function arrayOf<T>(guard: Guard<T>): Guard<T[]> {
  return (value): value is T[] => Array.isArray(value) && value.every(guard);
}

/**
 * Item-level checks stay plain predicates: the element type is already pinned
 * by the enclosing response guard, so a second type predicate would add noise
 * without adding safety.
 */
function everyItem(check: (value: unknown) => boolean) {
  return (value: unknown): boolean => Array.isArray(value) && value.every(check);
}

function oneOf<const T extends readonly string[]>(
  values: T,
): Guard<T[number]> {
  return (value): value is T[number] =>
    typeof value === "string" && (values as readonly string[]).includes(value);
}

function isNumberRecord(value: unknown): value is Record<string, number> {
  return isRecord(value) && Object.values(value).every(isNumber);
}

/** Every user-scoped response carries the tenant it was resolved under. */
function hasTenantScope(value: Record<string, unknown>): boolean {
  return isString(value.tenant_id) && isNumber(value.user_id);
}

const isMetadataSource = oneOf(["reviewed-fixture", "tmdb-snapshot", "movielens"] as const);
const isSourceStatus = oneOf(["complete", "partial", "unavailable"] as const);

export function isMovieState(value: unknown): value is MovieState {
  return (
    isRecord(value) &&
    isNumber(value.movie_id) &&
    isNumber(value.user_id) &&
    isString(value.tenant_id) &&
    isNumber(value.revision) &&
    isString(value.updated_at) &&
    nullable(isNumber)(value.rating) &&
    nullable(isString)(value.rating_updated_at) &&
    nullable(isString)(value.watched_at) &&
    nullable(isString)(value.watchlisted_at) &&
    nullable(isString)(value.dismissed_at)
  );
}

function isRecommendationItem(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNumber(value.movie_id) &&
    isString(value.title) &&
    isNumber(value.score) &&
    isString(value.reason) &&
    arrayOf(isString)(value.genres) &&
    isMetadataSource(value.metadata_source) &&
    nullable(isNumber)(value.release_year) &&
    nullable(isString)(value.overview) &&
    nullable(isString)(value.poster_url) &&
    nullable(isString)(value.tmdb_id)
  );
}

/**
 * The policy block is what licenses the route's copy: `learned` decides
 * between fallback and learned-serving language, the count and threshold drive
 * the progress toward learned serving, and `score_scale` is what stops a rank
 * score being rendered as a match percentage. A response that cannot answer
 * those questions cannot be described truthfully, so it fails the boundary
 * rather than rendering with a guess.
 *
 * Quick Picks also states which filter ran and how many titles it removed, so
 * those two are checked here rather than read hopefully at the call site.
 */
function isServingPolicy(value: unknown): boolean {
  return (
    isRecord(value) &&
    isString(value.name) &&
    isBoolean(value.learned) &&
    isNumber(value.positive_signal_count) &&
    isNumber(value.threshold) &&
    isString(value.score_scale) &&
    isString(value.reason) &&
    isString(value.filter_policy) &&
    isNumber(value.excluded_count)
  );
}

export function isRecommendationResponse(
  value: unknown,
): value is RecommendationResponse {
  return (
    isRecord(value) &&
    hasTenantScope(value) &&
    isString(value.model_version) &&
    isString(value.policy) &&
    isServingPolicy(value.serving_policy) &&
    everyItem(isRecommendationItem)(value.items)
  );
}

function isHistoryItem(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNumber(value.movie_id) &&
    isString(value.title) &&
    isNumber(value.timestamp) &&
    arrayOf(isString)(value.genres) &&
    nullable(isNumber)(value.rating)
  );
}

export function isHistoryResponse(value: unknown): value is HistoryResponse {
  return (
    isRecord(value) && hasTenantScope(value) && everyItem(isHistoryItem)(value.items)
  );
}

function isTmdbRating(value: unknown): boolean {
  return isRecord(value) && isNumber(value.average) && isNumber(value.count);
}

function isCastMember(value: unknown): boolean {
  return (
    isRecord(value) &&
    isString(value.name) &&
    nullable(isString)(value.character) &&
    nullable(isString)(value.profile_url)
  );
}

function isTrailer(value: unknown): boolean {
  return (
    isRecord(value) &&
    // The only provider the page knows how to embed. Anything else is a record
    // this UI cannot render, so it fails the boundary rather than building a
    // URL against a host it has never heard of.
    value.provider === "youtube" &&
    isString(value.key) &&
    isString(value.name)
  );
}

/**
 * The enriched TMDB block on a detail record.
 *
 * Every field is optional in meaning but present in shape: the endpoint sends
 * explicit nulls rather than omitting keys, so a missing key is drift worth
 * catching. The page degrades on `details: null` — it does not need a guard
 * that tolerates half a details object.
 */
function isMovieDetails(value: unknown): boolean {
  return (
    isRecord(value) &&
    nullable(isString)(value.tagline) &&
    nullable(isNumber)(value.runtime_minutes) &&
    nullable(isString)(value.release_date) &&
    nullable(isString)(value.backdrop_url) &&
    (value.tmdb_rating === null || isTmdbRating(value.tmdb_rating)) &&
    arrayOf(isString)(value.directors) &&
    everyItem(isCastMember)(value.cast) &&
    (value.trailer === null || isTrailer(value.trailer)) &&
    isString(value.fetched_at)
  );
}

export function isCatalogItem(value: unknown): value is CatalogItem {
  return (
    isRecord(value) &&
    isNumber(value.movie_id) &&
    isString(value.title) &&
    isNumber(value.interaction_count) &&
    arrayOf(isString)(value.genres) &&
    isMetadataSource(value.metadata_source) &&
    isSourceStatus(value.source_status) &&
    nullable(isNumber)(value.release_year) &&
    nullable(isString)(value.overview) &&
    nullable(isString)(value.poster_url) &&
    nullable(isString)(value.tmdb_id) &&
    nullable(isMovieState)(value.state)
  );
}

/**
 * Detail's own item type: a catalog item that also carries the enriched block.
 *
 * `details` is required and nullable rather than optional, matching the API — a
 * detail record that omits the key entirely is drift, and the page would render
 * a degraded movie without anything having reported a problem.
 */
function isMovieDetailItem(value: unknown): value is MovieDetailItem {
  if (!isRecord(value)) return false;
  // Read before `isCatalogItem` narrows the value to the list type, which has
  // no `details` to reach for.
  const details = value.details;
  return (
    isCatalogItem(value) &&
    details !== undefined &&
    (details === null || isMovieDetails(details))
  );
}

function isCursorPage(value: unknown): boolean {
  return (
    isRecord(value) && isBoolean(value.has_more) && nullable(isString)(value.next_cursor)
  );
}

export function isCatalogResponse(value: unknown): value is CatalogResponse {
  return (
    isRecord(value) &&
    hasTenantScope(value) &&
    isCursorPage(value.page) &&
    everyItem(isCatalogItem)(value.items)
  );
}

export function isMovieDetailResponse(
  value: unknown,
): value is MovieDetailResponse {
  return isRecord(value) && hasTenantScope(value) && isMovieDetailItem(value.item);
}

function isLibraryMovie(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNumber(value.movie_id) &&
    isString(value.title) &&
    arrayOf(isString)(value.genres) &&
    isMovieState(value.state)
  );
}

function isLibraryCounts(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNumber(value.rated) &&
    isNumber(value.watchlist) &&
    isNumber(value.history)
  );
}

export function isLibraryResponse(value: unknown): value is LibraryResponse {
  return (
    isRecord(value) &&
    hasTenantScope(value) &&
    isCursorPage(value.page) &&
    isLibraryCounts(value.counts) &&
    oneOf(["rated", "watchlist", "history"] as const)(value.tab) &&
    oneOf(["recent", "title", "rating"] as const)(value.sort) &&
    nullable(isString)(value.query) &&
    everyItem(isLibraryMovie)(value.items)
  );
}

function isTasteGenre(value: unknown): boolean {
  return (
    isRecord(value) &&
    isString(value.genre) &&
    isNumber(value.rated_count) &&
    isNumber(value.average_rating)
  );
}

export function isTasteSummaryResponse(
  value: unknown,
): value is TasteSummaryResponse {
  return (
    isRecord(value) &&
    hasTenantScope(value) &&
    // The summary is only honest while it announces its own non-model source.
    value.source === "live-ratings-v1" &&
    isString(value.explanation) &&
    isString(value.generated_at) &&
    isNumber(value.rating_count) &&
    nullable(isNumber)(value.average_rating) &&
    everyItem(isTasteGenre)(value.top_genres)
  );
}

function isAuditPrediction(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNumber(value.movie_id) &&
    isNumber(value.score) &&
    isNumberRecord(value.features) &&
    // Rows written before candidate attribution existed report "unknown"
    // rather than omitting the field, so a string is always expected.
    isString(value.candidate_source) &&
    // The seed is optional: a popularity-fill candidate has no source item.
    (value.seed_movie_id === undefined || nullable(isNumber)(value.seed_movie_id))
  );
}

function isRecommendationAuditItem(value: unknown): boolean {
  return (
    isRecord(value) &&
    isString(value.request_id) &&
    // The caller-supplied correlation ID is how a UI matches an audit row to
    // the exact recommendation response it is explaining.
    isString(value.correlation_id) &&
    isString(value.policy) &&
    isString(value.model_version) &&
    isString(value.candidate_version) &&
    isString(value.ranker_version) &&
    isString(value.feature_version) &&
    isString(value.created_at) &&
    isNumber(value.latency_ms) &&
    nullable(isString)(value.fallback_reason) &&
    everyItem(isAuditPrediction)(value.predictions)
  );
}

export function isRecommendationAuditResponse(
  value: unknown,
): value is RecommendationAuditResponse {
  return (
    isRecord(value) &&
    hasTenantScope(value) &&
    everyItem(isRecommendationAuditItem)(value.items)
  );
}

export function isOnlineUserFeatures(
  value: unknown,
): value is OnlineUserFeatures {
  return (
    isRecord(value) &&
    hasTenantScope(value) &&
    isString(value.source) &&
    isString(value.feature_timestamp) &&
    nullable(isNumber)(value.user_interaction_count) &&
    nullable(isNumber)(value.user_days_active) &&
    nullable(isNumber)(value.user_days_since_last_interaction)
  );
}

export function isFeedbackMutationResponse(
  value: unknown,
): value is FeedbackMutationResponse {
  return (
    isRecord(value) &&
    isString(value.request_id) &&
    isBoolean(value.replayed) &&
    oneOf(["changed", "no_change"] as const)(value.outcome) &&
    isMovieState(value.state)
  );
}
