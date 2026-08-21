import "server-only";

/**
 * The one server-owned client for live product resources.
 *
 * Everything that reaches FastAPI on a user's behalf goes through here, which
 * is what makes the trust rules checkable in one place:
 *
 * - the access token comes from the Auth.js session and nowhere else, and a
 *   caller-supplied `Authorization` header is dropped rather than forwarded;
 * - every request carries an `X-Request-ID` so a UI failure can be traced to a
 *   FastAPI prediction audit row;
 * - every request is bounded by the resource's timeout; and
 * - a failure produces a failure state. This module imports no fixture, so
 *   there is no code path through which a production read can quietly return
 *   recorded data instead.
 */

import { requireApiAccessToken, type ApiSession } from "@/lib/bff-auth";
import {
  AUDITS,
  CATALOG,
  FEATURES,
  HISTORY,
  LIBRARY,
  MOVIE_DETAIL,
  RECOMMENDATIONS,
  TASTE_PROFILE,
  type ResourceDefinition,
} from "@/lib/resources/definitions";
import {
  readResourcePayload,
  resourceStateFromPayload,
  resourceStateFromTransportError,
} from "@/lib/resources/mapping";
import {
  newRequestId,
  REQUEST_ID_HEADER,
  sanitizeRequestId,
} from "@/lib/resources/request-id";
import { failureState, type ResourceState } from "@/lib/resources/state";

export type FetchLike = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

export type ResourceQuery = Record<
  string,
  string | number | boolean | null | undefined
>;

export type ResourceRequestOptions = {
  session: ApiSession;
  /** Reuses the caller's correlation ID when it is well-formed. */
  requestId?: string | null;
  timeoutMs?: number;
  /** Supplying a signal replaces the resource timeout with the caller's. */
  signal?: AbortSignal;
  fetchImpl?: FetchLike;
};

export function apiBaseUrl(): string {
  return process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";
}

function queryString(query: ResourceQuery | undefined): string {
  if (!query) return "";
  const search = new URLSearchParams();
  for (const [name, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(name, String(value));
  }
  return search.size ? `?${search.toString()}` : "";
}

function isAddressableId(value: number): boolean {
  return Number.isSafeInteger(value) && value > 0;
}

export async function fetchResource<T>(
  definition: ResourceDefinition<T>,
  path: string,
  options: ResourceRequestOptions & { query?: ResourceQuery },
): Promise<ResourceState<T>> {
  const requestId = sanitizeRequestId(options.requestId) ?? newRequestId();
  const accessToken = requireApiAccessToken(options.session);
  if (!accessToken) {
    return failureState({
      status: "auth-expired",
      resource: definition.name,
      reason: "session-expired",
      requestId,
    });
  }

  // Built from scratch on purpose: no caller gets to contribute credentials.
  const headers = new Headers({
    Accept: "application/json",
    Authorization: `Bearer ${accessToken}`,
    [REQUEST_ID_HEADER]: requestId,
  });

  let response: Response;
  try {
    response = await (options.fetchImpl ?? fetch)(
      `${apiBaseUrl()}${path}${queryString(options.query)}`,
      {
        cache: "no-store",
        headers,
        signal:
          options.signal ??
          AbortSignal.timeout(options.timeoutMs ?? definition.timeoutMs),
      },
    );
  } catch (error) {
    return resourceStateFromTransportError({
      resource: definition.name,
      requestId,
      error,
    });
  }

  return resourceStateFromPayload({
    definition,
    // FastAPI owns the audited ID once it has answered; prefer its value.
    requestId: sanitizeRequestId(response.headers.get(REQUEST_ID_HEADER)) ?? requestId,
    httpStatus: response.status,
    payload: await readResourcePayload(response),
  });
}

function unaddressable<T>(
  definition: ResourceDefinition<T>,
  requestId: string | null | undefined,
): ResourceState<T> {
  return failureState({
    status: "not-found",
    resource: definition.name,
    reason: "not-found",
    requestId: sanitizeRequestId(requestId) ?? newRequestId(),
  });
}

export function loadRecommendations(
  userId: number,
  options: ResourceRequestOptions & { limit?: number },
) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(RECOMMENDATIONS, options.requestId));
  return fetchResource(RECOMMENDATIONS, `/users/${userId}/recommendations`, {
    ...options,
    query: { limit: options.limit },
  });
}

export function loadHistory(
  userId: number,
  options: ResourceRequestOptions & { limit?: number },
) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(HISTORY, options.requestId));
  return fetchResource(HISTORY, `/users/${userId}/history`, {
    ...options,
    query: { limit: options.limit },
  });
}

export type CatalogQuery = {
  q?: string;
  genre?: string;
  year_from?: number;
  year_to?: number;
  sort?: "title" | "newest" | "popular";
  limit?: number;
  cursor?: string;
};

export function loadCatalog(
  userId: number,
  options: ResourceRequestOptions & { query?: CatalogQuery },
) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(CATALOG, options.requestId));
  return fetchResource(CATALOG, `/users/${userId}/catalog`, options);
}

export function loadMovieDetail(
  userId: number,
  movieId: number,
  options: ResourceRequestOptions,
) {
  if (!isAddressableId(userId) || !isAddressableId(movieId)) {
    return Promise.resolve(unaddressable(MOVIE_DETAIL, options.requestId));
  }
  return fetchResource(
    MOVIE_DETAIL,
    `/users/${userId}/movies/${movieId}`,
    options,
  );
}

export type LibraryQuery = {
  tab?: "rated" | "watchlist" | "history";
  sort?: "recent" | "title" | "rating";
  limit?: number;
  cursor?: string;
  q?: string;
};

export function loadLibrary(
  userId: number,
  options: ResourceRequestOptions & { query?: LibraryQuery },
) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(LIBRARY, options.requestId));
  return fetchResource(LIBRARY, `/users/${userId}/library`, options);
}

export function loadTasteProfile(userId: number, options: ResourceRequestOptions) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(TASTE_PROFILE, options.requestId));
  return fetchResource(TASTE_PROFILE, `/users/${userId}/taste-profile`, options);
}

export function loadRecommendationAudits(
  userId: number,
  options: ResourceRequestOptions & { limit?: number },
) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(AUDITS, options.requestId));
  return fetchResource(AUDITS, `/users/${userId}/audits`, {
    ...options,
    query: { limit: options.limit },
  });
}

export function loadOnlineFeatures(userId: number, options: ResourceRequestOptions) {
  if (!isAddressableId(userId)) return Promise.resolve(unaddressable(FEATURES, options.requestId));
  return fetchResource(FEATURES, `/users/${userId}/features`, options);
}
