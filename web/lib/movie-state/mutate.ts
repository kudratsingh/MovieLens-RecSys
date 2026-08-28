/**
 * The browser side of a canonical movie-state mutation.
 *
 * Every watched, rating, watchlist, and dismissal change from every surface —
 * Discover, Browse, movie detail, Library — goes through here so the rules the
 * write path depends on are enforced once rather than per button:
 *
 * - **Idempotency key.** One key per *intent*, not per HTTP attempt, so a
 *   retried mutation that may already have committed replays the original
 *   result instead of writing a second feedback event. A caller that offers a
 *   `Try again` supplies the key it used the first time; a caller that does not
 *   gets a fresh one per call.
 * - **Expected revision.** The revision we rendered is sent with the write, so
 *   a change made elsewhere is a `409` we can report rather than an overwrite.
 * - **Double-submit CSRF plus same-origin.** The BFF requires both; a token is
 *   fetched per attempt because the session may have rotated.
 * - **The committed response is the truth.** The optimistic value is discarded
 *   on success and replaced by the state the API actually committed, revision
 *   included. Nothing here invents a revision.
 * - **The committed answer is relayed.** Recording it in the tab-local store is
 *   part of committing, not a detail of one route: a watchlist set on detail has
 *   to be visible on the restored Browse card and on the Discover rail, and the
 *   only way that holds for every surface is if the write path does it.
 *
 * The access token is never touched: the BFF holds it, and this request goes
 * same-origin with the session cookie only.
 */

import type { FeedbackMutationResponse, MovieState } from "@/lib/api";
import type { MovieStateResource } from "@/lib/movie-state/actions";
import {
  recordCommittedState,
  type SessionStore,
} from "@/lib/movie-state/committed-store";
import type { ResourceDefinition } from "@/lib/resources/definitions";
import {
  readResourcePayload,
  resourceStateFromPayload,
  resourceStateFromTransportError,
  upstreamDetail,
} from "@/lib/resources/mapping";
import {
  newRequestId,
  REQUEST_ID_HEADER,
  sanitizeRequestId,
} from "@/lib/resources/request-id";
import {
  failureState,
  hasResourceData,
  isResourceFailure,
  type ResourceFailure,
} from "@/lib/resources/state";
import { isFeedbackMutationResponse } from "@/lib/resources/validate";

export type { MovieStateResource };

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
  /** Stable across retries of one user intent; minted per call when absent. */
  idempotencyKey?: string;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
  /** Overridable so a test can watch the relay without touching the tab's. */
  store?: SessionStore | null;
};

export type MovieStateMutationResult =
  | {
      status: "committed";
      state: MovieState;
      outcome: FeedbackMutationResponse["outcome"];
      replayed: boolean;
      requestId: string;
    }
  | {
      status: "conflict";
      requestId: string;
      detail: string | null;
      /**
       * What is actually stored, when the client managed to read it back while
       * recovering. Carried on the result so a caller adopts the truth without
       * issuing a second read of its own — three surfaces were each doing that
       * read separately, and only one of them was adopting the answer.
       */
      canonical?: MovieState | null;
    }
  | {
      status: "refused";
      requestId: string;
      /**
       * The API's own sentence, verbatim. It names a product rule this write
       * would break, and no copy written here could say it more precisely.
       */
      detail: string;
    }
  | { status: "failed"; failure: ResourceFailure };

/**
 * A `409` that means "somebody committed first", as opposed to one that means
 * "this transition is not allowed".
 *
 * The split is by body rather than by status because the API documents a single
 * `409` for three different conditions (`src/serving/app.py`: "Idempotency,
 * state revision, or transition conflict"). Two of them are races and are
 * recoverable — re-read the canonical record, replay the intent, and the write
 * lands. The third is a rule: `PUT .../watchlist` on a movie that is already
 * watched answers `409 {"detail": "a watched movie cannot be added to the
 * watchlist"}`, and nothing about retrying it can succeed. Reporting the rule
 * as a race told the viewer something untrue ("<title> changed somewhere else
 * before this saved") and spent a re-read plus a replay proving it.
 *
 * The two recoverable shapes are matched, and everything else is treated as a
 * refusal, because that is the safer direction to be wrong in: an unrecognised
 * `409` shown in the API's own words is honest and costs one retry the viewer
 * can make themselves, while an unrecognised `409` shown as a race is a
 * sentence the product invented about state nobody touched. A `409` with no
 * readable body stays a conflict — with nothing to render, the generic
 * recovery is all there is.
 *
 * The cleaner long-term fix is on the API side: a distinct status (`422`, or a
 * machine-readable `code` in the body) for a transition refusal would let the
 * client branch on the contract instead of on prose that a rewording could
 * silently move. This function is what the frontend can do without it.
 */
const CONCURRENCY_CONFLICT_DETAILS: readonly RegExp[] = [
  // `StateRevisionConflictError`: "state revision 3 is stale; current revision is 5"
  /^state revision\b/i,
  // `IdempotencyConflictError`: "idempotency key was already used for another
  // mutation" / "… with a different rating"
  /^idempotency key was already used\b/i,
];

export function isTransitionRefusal(detail: string | null): detail is string {
  if (detail === null) return false;
  const text = detail.trim();
  if (text === "") return false;
  return !CONCURRENCY_CONFLICT_DETAILS.some((pattern) => pattern.test(text));
}

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

/** `undefined` means "use the tab's store"; `null` means "do not relay". */
function relayStore(store: SessionStore | null | undefined): SessionStore | null {
  if (store !== undefined) return store;
  return typeof window === "undefined" ? null : window.sessionStorage;
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
      "Idempotency-Key": input.idempotencyKey ?? newIdempotencyKey(),
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

  const correlationId =
    sanitizeRequestId(response.headers.get(REQUEST_ID_HEADER)) ?? requestId;
  const payload = await readResourcePayload(response);
  // A revision or idempotency conflict is not a broken request: somebody else
  // — another tab, another device — committed first, and the viewer needs to
  // be told that rather than shown a generic failure. A transition refusal
  // arrives on the same status and is a different event entirely; see
  // `isTransitionRefusal` for why the two are told apart by body.
  if (response.status === 409) {
    const detail = upstreamDetail(payload);
    return isTransitionRefusal(detail)
      ? { status: "refused", requestId: correlationId, detail }
      : { status: "conflict", requestId: correlationId, detail };
  }

  const state = resourceStateFromPayload({
    definition: MOVIE_STATE_MUTATION,
    requestId: correlationId,
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
        requestId: correlationId,
      }),
    };
  }

  const store = relayStore(input.store);
  if (store) recordCommittedState(store, input.userId, state.data.state);

  return {
    status: "committed",
    state: state.data.state,
    outcome: state.data.outcome,
    replayed: state.data.replayed,
    requestId: state.requestId,
  };
}
