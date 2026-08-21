import "server-only";

/**
 * Route-handler side of the live-resource boundary.
 *
 * A BFF read route is three decisions — refuse forwarded credentials, pick a
 * correlation ID, translate a resource state into an HTTP answer — so they
 * live here instead of being retyped per route. Personalized payloads are
 * always `private, no-store`; they are a specific persona's state under a
 * specific tenant and must not land in a shared cache.
 */

import { BffSecurityError, rejectForwardedCredentials } from "@/lib/bff-security";
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

export const PRIVATE_NO_STORE = "private, no-store";

export function resourceRequestId(request: Request): string {
  return (
    sanitizeRequestId(request.headers.get(REQUEST_ID_HEADER)) ?? newRequestId()
  );
}

function failureStatus(failure: ResourceFailure): number {
  if (failure.status === "auth-expired") return 401;
  if (failure.status === "forbidden") return 403;
  if (failure.status === "not-found") return 404;
  if (failure.reason === "rate-limited") return 429;
  if (failure.reason === "timeout") return 504;
  return 502;
}

const FAILURE_DETAIL: Record<ResourceFailure["status"], string> = {
  "auth-expired": "Your session has expired. Sign in again.",
  forbidden: "This session is not allowed to read that resource.",
  "not-found": "That resource does not exist for the selected persona.",
  "upstream-error": "The recommendation API could not answer this request.",
};

export function resourceResponse<T>(state: ResourceState<T>): Response {
  const headers = new Headers({ "Cache-Control": PRIVATE_NO_STORE });

  if (hasResourceData(state)) {
    headers.set(REQUEST_ID_HEADER, state.requestId);
    return Response.json(state.data, { headers });
  }
  if (state.status === "loading" || state.status === "retry") {
    // A route handler resolves before it answers; a pending state here means a
    // caller wired something wrong rather than an upstream problem.
    return Response.json(
      { detail: "The resource was not resolved before responding." },
      { status: 500, headers },
    );
  }

  headers.set(REQUEST_ID_HEADER, state.requestId);
  return Response.json(
    { detail: state.detail ?? FAILURE_DETAIL[state.status], reason: state.reason },
    { status: failureStatus(state), headers },
  );
}

/**
 * Wraps a resource read as a BFF route response. The loader receives the
 * correlation ID so the browser, the BFF log line, and the FastAPI audit row
 * all agree on one value.
 */
export async function resourceRouteResponse<T>(
  request: Request,
  load: (requestId: string) => Promise<ResourceState<T>>,
): Promise<Response> {
  try {
    rejectForwardedCredentials(request);
  } catch (error) {
    if (!(error instanceof BffSecurityError)) throw error;
    return Response.json(
      { detail: error.message },
      { status: error.status, headers: { "Cache-Control": PRIVATE_NO_STORE } },
    );
  }

  const requestId = resourceRequestId(request);
  return resourceResponse(await load(requestId));
}
