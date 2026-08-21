/**
 * The resource-local state model every live product region shares.
 *
 * Regions load independently, so a region never reports "the page failed" — it
 * reports what happened to its own resource. Keeping the union small and
 * closed is what lets a failing recommendation rail sit next to a healthy
 * catalog grid without either one guessing about the other.
 */

export type LiveResourceName =
  | "recommendations"
  | "history"
  | "catalog"
  | "movie-detail"
  | "library"
  | "taste-profile"
  | "audits"
  | "features";

/**
 * `live` is the only value a production fetch can produce. Recorded fixtures
 * are tagged so a reviewer can tell at a glance which one they are looking at,
 * and so tests can assert that production never emits the fixture tag.
 */
export type ResourceSource = "live" | "recorded-contract-fixture";

export type ResourceFailureStatus =
  | "forbidden"
  | "auth-expired"
  | "not-found"
  | "upstream-error";

export type ResourceFailureReason =
  | "session-expired"
  | "forbidden"
  | "not-found"
  | "bad-request"
  | "rate-limited"
  | "server"
  | "timeout"
  | "network"
  | "invalid-json"
  | "invalid-payload";

export type ResourceFailure = {
  status: ResourceFailureStatus;
  resource: LiveResourceName;
  reason: ResourceFailureReason;
  /** Correlates the browser, the BFF, and the FastAPI prediction audit. */
  requestId: string;
  httpStatus: number | null;
  /** Upstream `detail`, kept for logs and BFF passthrough rather than UI copy. */
  detail: string | null;
  retryable: boolean;
};

export type ResourceState<T> =
  | { status: "loading"; resource: LiveResourceName }
  | {
      status: "retry";
      resource: LiveResourceName;
      attempt: number;
      previous: ResourceFailure;
    }
  | {
      status: "ready";
      resource: LiveResourceName;
      data: T;
      requestId: string;
      source: ResourceSource;
    }
  | {
      status: "empty";
      resource: LiveResourceName;
      data: T;
      requestId: string;
      source: ResourceSource;
    }
  | ResourceFailure;

export type ResourceStatus = ResourceState<unknown>["status"];

export const RESOURCE_STATUSES = [
  "loading",
  "retry",
  "ready",
  "empty",
  "forbidden",
  "auth-expired",
  "not-found",
  "upstream-error",
] as const satisfies readonly ResourceStatus[];

/** Reasons where asking again can plausibly produce a different answer. */
const RETRYABLE_REASONS = new Set<ResourceFailureReason>([
  "timeout",
  "network",
  "server",
  "rate-limited",
]);

export function loadingState(resource: LiveResourceName): ResourceState<never> {
  return { status: "loading", resource };
}

export function retryState(
  previous: ResourceFailure,
  attempt = 1,
): ResourceState<never> {
  return { status: "retry", resource: previous.resource, attempt, previous };
}

export function readyState<T>(
  resource: LiveResourceName,
  data: T,
  requestId: string,
  source: ResourceSource = "live",
): ResourceState<T> {
  return { status: "ready", resource, data, requestId, source };
}

export function emptyState<T>(
  resource: LiveResourceName,
  data: T,
  requestId: string,
  source: ResourceSource = "live",
): ResourceState<T> {
  return { status: "empty", resource, data, requestId, source };
}

export function failureState(input: {
  status: ResourceFailureStatus;
  resource: LiveResourceName;
  reason: ResourceFailureReason;
  requestId: string;
  httpStatus?: number | null;
  detail?: string | null;
}): ResourceFailure {
  return {
    status: input.status,
    resource: input.resource,
    reason: input.reason,
    requestId: input.requestId,
    httpStatus: input.httpStatus ?? null,
    detail: input.detail ?? null,
    retryable: RETRYABLE_REASONS.has(input.reason),
  };
}

export function isResourceFailure<T>(
  state: ResourceState<T>,
): state is ResourceFailure {
  return (
    state.status === "forbidden" ||
    state.status === "auth-expired" ||
    state.status === "not-found" ||
    state.status === "upstream-error"
  );
}

export function hasResourceData<T>(
  state: ResourceState<T>,
): state is Extract<ResourceState<T>, { data: T }> {
  return state.status === "ready" || state.status === "empty";
}
