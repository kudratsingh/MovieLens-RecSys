/**
 * The one seam Discover writes its presentation preference through.
 *
 * Same rules as the movie-state write path (`lib/movie-state/mutate.ts`), for
 * the same reasons and no others: the access token stays in the BFF session, the
 * request goes same-origin with the Auth.js double-submit CSRF token, the
 * revision we rendered is asserted so a change made in another tab is a `409`
 * we can correct rather than an overwrite, and the committed response replaces
 * the local value outright.
 *
 * It is a smaller path than the movie-state one on purpose. There is no
 * idempotency key, because a full-object PUT repeated is the same request and
 * the API reports the repeat as `no_change`; and there is no relay, because
 * nothing else on the page renders this value.
 *
 * The conflict recovery is the one thing worth copying in full: read what is
 * actually stored, replay the same intent against it, exactly once. A viewer
 * pressing a toggle means "make it this", and a stale revision is a reason to
 * re-address the write, not to discard it.
 */

import type { UserPreferences, UserPreferencesMutation } from "@/lib/api";
import {
  DEFAULT_FEATURED_PREFERENCE,
  type FeaturedPreference,
} from "@/lib/discover/featured-preference";
import { readBffResource } from "@/lib/resources/browser";
import { PREFERENCES, type ResourceDefinition } from "@/lib/resources/definitions";
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
import { isUserPreferencesMutation } from "@/lib/resources/validate";

/** The write shares the read's resource identity: one region, one label. */
const PREFERENCE_MUTATION: ResourceDefinition<UserPreferencesMutation> = {
  name: "preferences",
  label: PREFERENCES.label,
  timeoutMs: 6_000,
  guard: isUserPreferencesMutation,
};

export type PreferenceMutationInput = {
  userId: number;
  featureWatchlistedTitles: boolean;
  /** The revision the toggle rendered. `0` means "no stored row yet". */
  expectedRevision: number;
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
};

export type PreferenceMutationResult =
  | {
      status: "committed";
      preference: FeaturedPreference;
      outcome: "changed" | "no_change";
      requestId: string;
    }
  | {
      status: "conflict";
      requestId: string;
      detail: string | null;
      /** What is stored, when it could be read back while recovering. */
      canonical: FeaturedPreference | null;
    }
  | { status: "failed"; failure: ResourceFailure };

export type PreferenceClient = {
  set(input: PreferenceMutationInput): Promise<PreferenceMutationResult>;
};

function preferenceOf(value: UserPreferences): FeaturedPreference {
  return {
    featureWatchlistedTitles: value.feature_watchlisted_titles,
    revision: value.revision,
  };
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

export function preferencePath(input: {
  userId: number;
  expectedRevision: number;
}): string {
  return (
    `/api/users/${input.userId}/preferences` +
    `?expected_revision=${encodeURIComponent(String(Math.max(0, input.expectedRevision)))}`
  );
}

async function attempt(
  input: PreferenceMutationInput,
): Promise<PreferenceMutationResult> {
  const fetchImpl = input.fetchImpl ?? fetch;
  const requestId = newRequestId();

  let response: Response;
  try {
    response = await fetchImpl(preferencePath(input), {
      method: "PUT",
      cache: "no-store",
      credentials: "same-origin",
      headers: new Headers({
        Accept: "application/json",
        "Content-Type": "application/json",
        [REQUEST_ID_HEADER]: requestId,
        "x-csrf-token": await csrfToken(fetchImpl, input.signal),
      }),
      body: JSON.stringify({
        feature_watchlisted_titles: input.featureWatchlistedTitles,
      }),
      signal: input.signal,
    });
  } catch (error) {
    return {
      status: "failed",
      failure: resourceStateFromTransportError({
        resource: PREFERENCE_MUTATION.name,
        requestId,
        error,
      }),
    };
  }

  const correlationId =
    sanitizeRequestId(response.headers.get(REQUEST_ID_HEADER)) ?? requestId;
  const payload = await readResourcePayload(response);
  // The only `409` this endpoint raises is a stale revision — there is no
  // transition rule to refuse and no idempotency key to collide — so unlike the
  // movie-state path there is nothing here to tell apart by body.
  if (response.status === 409) {
    return {
      status: "conflict",
      requestId: correlationId,
      detail: upstreamDetail(payload),
      canonical: null,
    };
  }

  const state = resourceStateFromPayload({
    definition: PREFERENCE_MUTATION,
    requestId: correlationId,
    httpStatus: response.status,
    payload,
  });
  if (isResourceFailure(state)) return { status: "failed", failure: state };
  if (!hasResourceData(state)) {
    return {
      status: "failed",
      failure: failureState({
        status: "upstream-error",
        resource: PREFERENCE_MUTATION.name,
        reason: "invalid-payload",
        requestId: correlationId,
      }),
    };
  }
  return {
    status: "committed",
    preference: preferenceOf(state.data.preferences),
    outcome: state.data.outcome,
    requestId: state.requestId,
  };
}

async function readCanonical(
  userId: number,
  fetchImpl?: typeof fetch,
): Promise<FeaturedPreference | null> {
  const state = await readBffResource(
    PREFERENCES,
    `/api/users/${userId}/preferences`,
    { fetchImpl },
  );
  return hasResourceData(state) ? preferenceOf(state.data) : null;
}

export function createBffPreferenceClient(
  fetchImpl?: typeof fetch,
): PreferenceClient {
  return {
    async set(input) {
      const fetchForCall = input.fetchImpl ?? fetchImpl;
      const call = { ...input, fetchImpl: fetchForCall };
      const first = await attempt(call);
      if (first.status !== "conflict") return first;

      const canonical = await readCanonical(input.userId, fetchForCall);
      if (!canonical) return first;
      // Replaying the assertion that was just refused would only earn the same
      // answer, so that is a genuine conflict rather than a stale render.
      if (canonical.revision === input.expectedRevision) {
        return { ...first, canonical };
      }
      const replay = await attempt({ ...call, expectedRevision: canonical.revision });
      return replay.status === "conflict" ? { ...replay, canonical } : replay;
    },
  };
}

export const bffPreferenceClient: PreferenceClient = createBffPreferenceClient();

/** A client for the recorded preview surfaces: commits in memory, never over the wire. */
export function inMemoryPreferenceClient(
  initial: FeaturedPreference = DEFAULT_FEATURED_PREFERENCE,
): PreferenceClient {
  let held = initial;
  return {
    set(input) {
      const changed = held.featureWatchlistedTitles !== input.featureWatchlistedTitles;
      held = {
        featureWatchlistedTitles: input.featureWatchlistedTitles,
        revision: changed ? held.revision + 1 : held.revision,
      };
      return Promise.resolve({
        status: "committed",
        preference: held,
        outcome: changed ? "changed" : "no_change",
        requestId: newRequestId(),
      });
    },
  };
}
