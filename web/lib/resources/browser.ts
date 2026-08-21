/**
 * Browser-side reader for the same-origin BFF.
 *
 * Client components get the identical state model as server components, and
 * they get it without ever holding a token: the access token lives in the
 * Auth.js server session, so this reader builds its own headers and refuses a
 * caller-supplied credential outright instead of quietly dropping it. A silent
 * drop would let a bearer-forwarding mistake survive review.
 */

import type { ResourceDefinition } from "@/lib/resources/definitions";
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
import type { ResourceState } from "@/lib/resources/state";

const FORWARDED_CREDENTIAL_HEADERS = [
  "authorization",
  "proxy-authorization",
] as const;

export class ForwardedCredentialError extends Error {
  constructor(header: string) {
    super(
      `Browser requests must not carry a ${header} header; the BFF holds the access token.`,
    );
    this.name = "ForwardedCredentialError";
  }
}

export type BffReadOptions = {
  requestId?: string | null;
  timeoutMs?: number;
  /** Supplying a signal replaces the resource timeout with the caller's. */
  signal?: AbortSignal;
  headers?: HeadersInit;
  fetchImpl?: typeof fetch;
};

export async function readBffResource<T>(
  definition: ResourceDefinition<T>,
  url: string,
  options: BffReadOptions = {},
): Promise<ResourceState<T>> {
  const headers = new Headers(options.headers);
  for (const name of FORWARDED_CREDENTIAL_HEADERS) {
    if (headers.has(name)) throw new ForwardedCredentialError(name);
  }

  const requestId = sanitizeRequestId(options.requestId) ?? newRequestId();
  headers.set("Accept", "application/json");
  headers.set(REQUEST_ID_HEADER, requestId);

  let response: Response;
  try {
    response = await (options.fetchImpl ?? fetch)(url, {
      cache: "no-store",
      credentials: "same-origin",
      headers,
      signal:
        options.signal ??
        AbortSignal.timeout(options.timeoutMs ?? definition.timeoutMs),
    });
  } catch (error) {
    return resourceStateFromTransportError({
      resource: definition.name,
      requestId,
      error,
    });
  }

  return resourceStateFromPayload({
    definition,
    requestId: sanitizeRequestId(response.headers.get(REQUEST_ID_HEADER)) ?? requestId,
    httpStatus: response.status,
    payload: await readResourcePayload(response),
  });
}
