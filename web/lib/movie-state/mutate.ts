/**
 * The browser side of a canonical movie-state mutation.
 *
 * Every watched, rating, watchlist, and dismissal change goes through here so
 * the four rules the write path depends on are enforced once rather than per
 * button:
 *
 * - **Idempotency key.** One key per attempt, so a retried or duplicated
 *   request replays the original commit instead of applying twice.
 * - **Expected revision.** The revision we rendered is sent with the write, so
 *   a change made elsewhere is a `409` we can report rather than an overwrite.
 * - **Double-submit CSRF plus same-origin.** The BFF requires both; a token is
 *   fetched per attempt because the session may have rotated.
 * - **The committed response is the truth.** The optimistic value is discarded
 *   on success and replaced by the state the API actually committed, revision
 *   included. Nothing here invents a revision.
 *
 * The access token is never touched: the BFF holds it, and this request goes
 * same-origin with the session cookie only.
 */

import type { FeedbackMutationResponse, MovieState } from "@/lib/api";
import type { ResourceDefinition } from "@/lib/resources/definitions";
import {
  readResourcePayload,
  resourceStateFromPayload,
  resourceStateFromTransportError,
  upstreamDetail,
} from "@/lib/resources/mapping";
import { newRequestId, REQUEST_ID_HEADER } from "@/lib/resources/request-id";
import {
  failureState,
  hasResourceData,
  isResourceFailure,
  type ResourceFailure,
} from "@/lib/resources/state";
import { isFeedbackMutationResponse } from "@/lib/resources/validate";

export type MovieStateResource = "watched" | "rating" | "watchlist" | "dismissal";

/**
 * A mutation failure is rendered beside the control that produced it, so it
 * reuses the movie-detail resource identity rather than adding a region name
 * no route ever loads on its own.
 */
const MOVIE_STATE_MUTATION: ResourceDefinition<FeedbackMutationResponse> = {
  name: "movie-detail",
  label: "Movie state",
  timeoutMs: 6_000,
  guard: isFeedbackMutationResponse,
};

export type MovieStateMutationInput = {
  userId: number;
  movieId: number;
  resource: MovieStateResource;
  method: "PUT" | "DELETE";
  /** Required for `PUT rating`; ignored otherwise. */
  rating?: number;
  /** The revision the control rendered. `0` means "no state yet". */
  expectedRevision: number;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
};

export type MovieStateMutationResult =
  | {
      status: "committed";
      state: MovieState;
      outcome: FeedbackMutationResponse["outcome"];
      replayed: boolean;
      requestId: string;
    }
  | { status: "conflict"; requestId: string; detail: string | null }
  | { status: "failed"; failure: ResourceFailure };

/**
 * The API types the idempotency key as a UUID. `crypto.randomUUID` is absent
 * in a few test and insecure-context runtimes, so fall back to a v4-shaped
 * value built from whatever randomness is available.
 */
export function newIdempotencyKey(): string {
  const source = globalThis.crypto;
  if (source && typeof source.randomUUID === "function") return source.randomUUID();

  const bytes = new Uint8Array(16);
  if (source && typeof source.getRandomValues === "function") {
    source.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

async function csrfToken(
  fetchImpl: typeof fetch,
  signal?: AbortSignal,
): Promise<string> {
  const response = await fetchImpl("/api/auth/csrf", { cache: "no-store", signal });
  if (!response.ok) throw new Error("Could not obtain a CSRF token");
  const body = (await response.json()) as { csrfToken?: unknown };
  if (typeof body.csrfToken !== "string" || !body.csrfToken) {
    throw new Error("Could not obtain a CSRF token");
  }
  return body.csrfToken;
}

export function movieStatePath(input: {
  userId: number;
  movieId: number;
  resource: MovieStateResource;
  expectedRevision: number;
}): string {
  return (
    `/api/users/${input.userId}/movies/${input.movieId}/${input.resource}` +
    `?expected_revision=${encodeURIComponent(String(Math.max(0, input.expectedRevision)))}`
  );
}

export async function mutateMovieState(
  input: MovieStateMutationInput,
): Promise<MovieStateMutationResult> {
  const fetchImpl = input.fetchImpl ?? fetch;
  const requestId = newRequestId();

  let response: Response;
  try {
    const headers = new Headers({
      Accept: "application/json",
      "Idempotency-Key": newIdempotencyKey(),
      [REQUEST_ID_HEADER]: requestId,
      "x-csrf-token": await csrfToken(fetchImpl, input.signal),
    });
    const sendsBody = input.method === "PUT" && input.resource === "rating";
    if (sendsBody) headers.set("Content-Type", "application/json");

    response = await fetchImpl(movieStatePath(input), {
      method: input.method,
      cache: "no-store",
      credentials: "same-origin",
      headers,
      body: sendsBody ? JSON.stringify({ rating: input.rating }) : undefined,
      signal: input.signal,
    });
  } catch (error) {
    return {
      status: "failed",
      failure: resourceStateFromTransportError({
        resource: MOVIE_STATE_MUTATION.name,
        requestId,
        error,
      }),
    };
  }

  const payload = await readResourcePayload(response);
  // A revision or idempotency conflict is not a broken request: somebody else
  // — another tab, another device — committed first, and the viewer needs to
  // be told that rather than shown a generic failure.
  if (response.status === 409) {
    return { status: "conflict", requestId, detail: upstreamDetail(payload) };
  }

  const state = resourceStateFromPayload({
    definition: MOVIE_STATE_MUTATION,
    requestId,
    httpStatus: response.status,
    payload,
  });
  if (isResourceFailure(state)) return { status: "failed", failure: state };
  if (!hasResourceData(state)) {
    // `resourceStateFromPayload` always resolves to data or a failure; this
    // arm only exists to keep the union total.
    return {
      status: "failed",
      failure: failureState({
        status: "upstream-error",
        resource: MOVIE_STATE_MUTATION.name,
        reason: "invalid-payload",
        requestId,
      }),
    };
  }

  return {
    status: "committed",
    state: state.data.state,
    outcome: state.data.outcome,
    replayed: state.data.replayed,
    requestId: state.requestId,
  };
}
