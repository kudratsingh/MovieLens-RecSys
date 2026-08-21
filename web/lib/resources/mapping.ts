/**
 * Outcome → state mapping shared by the server client and the browser reader.
 *
 * Both sides must agree, otherwise the same upstream 403 would read as
 * "forbidden" on a server-rendered region and "something went wrong" on a
 * client-rendered one.
 */

import type { ResourceDefinition } from "@/lib/resources/definitions";
import {
  emptyState,
  failureState,
  readyState,
  type LiveResourceName,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";
import { isRecord } from "@/lib/resources/validate";

/** Marks a body that could not be parsed as JSON at all. */
export const INVALID_JSON = Symbol("invalid-json");

export type ResourcePayload = unknown | typeof INVALID_JSON;

export function upstreamDetail(payload: ResourcePayload): string | null {
  if (!isRecord(payload)) return null;
  return typeof payload.detail === "string" ? payload.detail : null;
}

export async function readResourcePayload(
  response: Response,
): Promise<ResourcePayload> {
  if (response.status === 204) return null;
  try {
    return (await response.json()) as unknown;
  } catch {
    return INVALID_JSON;
  }
}

export function resourceStateFromPayload<T>(input: {
  definition: ResourceDefinition<T>;
  requestId: string;
  httpStatus: number;
  payload: ResourcePayload;
}): ResourceState<T> {
  const { definition, requestId, httpStatus, payload } = input;
  const resource = definition.name;
  const detail = upstreamDetail(payload);
  const failed = (
    status: ResourceFailure["status"],
    reason: ResourceFailure["reason"],
  ) => failureState({ status, resource, reason, requestId, httpStatus, detail });

  if (httpStatus === 401) return failed("auth-expired", "session-expired");
  if (httpStatus === 403) return failed("forbidden", "forbidden");
  if (httpStatus === 404) return failed("not-found", "not-found");
  if (httpStatus === 429) return failed("upstream-error", "rate-limited");
  // 400/409/422 mean the request we composed was wrong. That is our defect, so
  // it is surfaced as an upstream error rather than blamed on the reader.
  if (httpStatus >= 400 && httpStatus < 500) {
    return failed("upstream-error", "bad-request");
  }
  if (httpStatus >= 500) return failed("upstream-error", "server");

  if (httpStatus === 204) {
    return failed("upstream-error", "invalid-payload");
  }
  if (payload === INVALID_JSON) {
    return failed("upstream-error", "invalid-json");
  }
  if (!definition.guard(payload)) {
    return failed("upstream-error", "invalid-payload");
  }

  return definition.isEmpty?.(payload)
    ? emptyState(resource, payload, requestId)
    : readyState(resource, payload, requestId);
}

function errorName(value: unknown): string {
  if (typeof value !== "object" || value === null) return "";
  const named = value as { name?: unknown; cause?: unknown };
  if (typeof named.name === "string" && named.name !== "Error") return named.name;
  return errorName(named.cause);
}

export function resourceStateFromTransportError(input: {
  resource: LiveResourceName;
  requestId: string;
  error: unknown;
}): ResourceFailure {
  const name = errorName(input.error);
  const timedOut = name === "TimeoutError" || name === "AbortError";
  return failureState({
    status: "upstream-error",
    resource: input.resource,
    reason: timedOut ? "timeout" : "network",
    requestId: input.requestId,
  });
}
