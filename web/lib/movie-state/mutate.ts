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
  upstreamCode,
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
      /**
       * What is actually stored, read back while recovering — the same field
       * a conflict carries. A refusal proves this client's picture of the
       * movie was wrong (the rule it broke is a rule about state), so the
       * caller corrects its control from this rather than leaving a viewer
       * looking at something the API has just contradicted.
       */
      canonical?: MovieState | null;
      /**
       * Whether that read actually moved anything. Derived from the revision
       * the refused write asserted, which only the write path knows, so it is
       * settled once here instead of in each of the four surfaces.
       */
      corrected?: boolean;
    }
  | { status: "failed"; failure: ResourceFailure };

/**
 * The API's stable names for the two ways a well-formed write is turned away.
 *
 * They are the contract; the sentence beside them is copy and may be reworded
 * at any time. This client used to split the two by matching that sentence
 * (issue #74) — six regexes against prose in `src/serving/feedback.py` — which
 * meant a copy edit on the server could silently turn a rule into a "somebody
 * else changed this" prompt.
 */
export const TRANSITION_REFUSED = "transition_refused";
export const REVISION_CONFLICT = "revision_conflict";
export const IDEMPOTENCY_CONFLICT = "idempotency_conflict";

/**
 * Which of the two the API just answered, or neither.
 *
 * A refusal is a rule — `PUT .../watchlist` on a title that is already watched
 * — and no retry can succeed. A conflict is a race (a stale `expected_revision`
 * or a reused idempotency key) and the write path recovers from it by re-reading
 * and replaying. Telling them apart wrongly is expensive in both directions: a
 * rule reported as a race tells the viewer something untrue about state nobody
 * touched, and a race reported as a rule abandons a write that would have landed.
 *
 * The `code` decides it. The status is the fallback for a body without one, and
 * on `422` it is deliberately not enough on its own — that is also what FastAPI
 * answers for a request that failed validation. What separates the two there is
 * the sentence: a refusal carries one and a validation error carries a list of
 * field errors instead, so the caller below requires a readable `detail` before
 * it treats a `422` as a rule.
 */
function mutationRefusal(
  status: number,
  code: string | null,
): "refused" | "conflict" | null {
  if (code === TRANSITION_REFUSED || (code === null && status === 422)) return "refused";
  if (code === REVISION_CONFLICT || code === IDEMPOTENCY_CONFLICT) return "conflict";
  return status === 409 ? "conflict" : null;
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
  // Neither of these is a broken request, and neither is a generic failure:
  // a conflict means somebody else — another tab, another device — committed
  // first, and a refusal means the API understood the write and a product rule
  // forbids it. See `mutationRefusal` for how the two are told apart.
  const detail = upstreamDetail(payload);
  const refusal = mutationRefusal(response.status, upstreamCode(payload));
  // A refusal is shown in the API's own words, so one that arrived without a
  // sentence — a validation error's list of field errors, or a malformed body
  // — is not a rule to render blank. It falls through to the generic failure
  // mapping below, which is right: that is our defect, not a product rule.
  if (refusal === "refused" && detail !== null) {
    return { status: "refused", requestId: correlationId, detail };
  }
  if (refusal === "conflict") {
    return { status: "conflict", requestId: correlationId, detail };
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
