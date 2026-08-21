/**
 * Browser-side Library reads and writes.
 *
 * Reads go through the shared live-resource reader so a Library region reports
 * the same state model as every other region. Writes cannot: they need a
 * method, a body, an idempotency key, and a CSRF token, so they are assembled
 * here and then mapped through the *same* outcome translation, which is what
 * keeps a 401 during a mutation and a 401 during a read from telling the reader
 * two different stories.
 *
 * Three properties are deliberate rather than incidental:
 *
 * - No request here sets `Authorization`. The access token lives in the Auth.js
 *   server session and only the BFF may attach it.
 * - The idempotency key belongs to the user's intent, not to the HTTP attempt,
 *   so retrying a mutation that may already have committed replays the original
 *   result instead of writing a second event.
 * - `expected_revision` is always the revision the row was rendered from, so a
 *   write against a stale row is refused by the API rather than silently
 *   clobbering a change made somewhere else.
 *
 * This module imports no fixture. The recorded Library preview supplies its own
 * client object instead, so there is no path by which a live route can fall
 * back to recorded data.
 */

import type {
  FeedbackMutationResponse,
  LibraryResponse,
  MovieState,
  TasteSummaryResponse,
} from "@/lib/api";
import type { FeedbackResource } from "@/lib/library/collection";
import {
  LIBRARY_PAGE_SIZE,
  type LibrarySort,
  type LibraryTab,
} from "@/lib/library/url-state";
import { readBffResource } from "@/lib/resources/browser";
import {
  LIBRARY,
  MOVIE_DETAIL,
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
import {
  hasResourceData,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";
import { isFeedbackMutationResponse } from "@/lib/resources/validate";

export type LibraryReadOptions = {
  userId: number;
  tab: LibraryTab;
  sort: LibrarySort;
  query: string;
  cursor: string | null;
  limit?: number;
  signal?: AbortSignal;
};

export type LibraryMutationRequest = {
  userId: number;
  movieId: number;
  resource: FeedbackResource;
  method: "PUT" | "DELETE";
  rating?: number;
  expectedRevision: number;
  /** Stable across retries of the same user intent. */
  idempotencyKey: string;
};

export type LibraryMutationResult =
  | {
      status: "committed";
      state: MovieState;
      requestId: string;
      /** True when the API replayed an earlier identical request. */
      replayed: boolean;
    }
  | { status: "conflict"; requestId: string }
  | ResourceFailure;

export type LibraryClient = {
  readLibrary(options: LibraryReadOptions): Promise<ResourceState<LibraryResponse>>;
  readTasteProfile(userId: number): Promise<ResourceState<TasteSummaryResponse>>;
  /** Used to recover a row after the API reports a revision conflict. */
  readMovieState(userId: number, movieId: number): Promise<MovieState | null>;
  mutate(request: LibraryMutationRequest): Promise<LibraryMutationResult>;
};

/**
 * A mutation answers with the committed canonical state, so it is validated as
 * strictly as a read is. It reports under the Library resource because that is
 * the region a failed write is shown in.
 */
const MOVIE_STATE_MUTATION: ResourceDefinition<FeedbackMutationResponse> = {
  name: "library",
  label: "Movie state",
  timeoutMs: 6_000,
  guard: isFeedbackMutationResponse,
};

export function libraryReadUrl(options: LibraryReadOptions): string {
  const params = new URLSearchParams({
    tab: options.tab,
    sort: options.sort,
    limit: String(options.limit ?? LIBRARY_PAGE_SIZE),
  });
  if (options.query) params.set("q", options.query);
  if (options.cursor) params.set("cursor", options.cursor);
  return `/api/users/${options.userId}/library?${params}`;
}

async function csrfToken(fetchImpl: typeof fetch): Promise<string> {
  const response = await fetchImpl("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Could not create a secure feedback request");
  }
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}

export function createBffLibraryClient(
  fetchImpl: typeof fetch = fetch,
): LibraryClient {
  return {
    readLibrary(options) {
      return readBffResource(LIBRARY, libraryReadUrl(options), {
        fetchImpl,
        signal: options.signal,
      });
    },

    readTasteProfile(userId) {
      return readBffResource(
        TASTE_PROFILE,
        `/api/users/${userId}/taste-profile`,
        { fetchImpl },
      );
    },

    async readMovieState(userId, movieId) {
      const state = await readBffResource(
        MOVIE_DETAIL,
        `/api/users/${userId}/movies/${movieId}`,
        { fetchImpl },
      );
      return hasResourceData(state) ? (state.data.item.state ?? null) : null;
    },

    async mutate(request) {
      const requestId = newRequestId();
      const url =
        `/api/users/${request.userId}/movies/${request.movieId}/${request.resource}` +
        `?expected_revision=${request.expectedRevision}`;

      let response: Response;
      try {
        response = await fetchImpl(url, {
          method: request.method,
          cache: "no-store",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": request.idempotencyKey,
            [REQUEST_ID_HEADER]: requestId,
            "x-csrf-token": await csrfToken(fetchImpl),
          },
          body:
            request.resource === "rating" && request.method === "PUT"
              ? JSON.stringify({ rating: request.rating })
              : undefined,
        });
      } catch (error) {
        return resourceStateFromTransportError({
          resource: MOVIE_STATE_MUTATION.name,
          requestId,
          error,
        });
      }

      const correlationId =
        sanitizeRequestId(response.headers.get(REQUEST_ID_HEADER)) ?? requestId;

      // A 409 is not a malformed request: the row was written from a revision
      // that is no longer current, or the key was reused for a different
      // mutation. Either way the fix is to show what is actually stored.
      if (response.status === 409) {
        return { status: "conflict", requestId: correlationId };
      }

      const state = resourceStateFromPayload({
        definition: MOVIE_STATE_MUTATION,
        requestId: correlationId,
        httpStatus: response.status,
        payload: await readResourcePayload(response),
      });
      if (!hasResourceData(state)) {
        // `resourceStateFromPayload` only ever resolves to ready, empty, or a
        // failure; a pending state is not reachable from a settled response.
        return state as ResourceFailure;
      }
      return {
        status: "committed",
        state: state.data.state,
        requestId: state.requestId,
        replayed: state.data.replayed,
      };
    },
  };
}

export const bffLibraryClient = createBffLibraryClient();
