import "server-only";

import { apiAuthorization } from "@/lib/bff-auth";
import {
  newRequestId,
  REQUEST_ID_HEADER,
  sanitizeRequestId,
} from "@/lib/resources/request-id";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

export async function proxyRecommendationApi(
  accessToken: string,
  path: string,
  init: RequestInit = {},
  requestId?: string | null,
): Promise<Response> {
  const correlationId = sanitizeRequestId(requestId) ?? newRequestId();
  const headers = new Headers(init.headers);
  // The session token is the only credential that may reach FastAPI, so a
  // caller-contributed one is dropped before the real header is written.
  headers.delete("authorization");
  headers.delete("proxy-authorization");
  for (const [name, value] of Object.entries(apiAuthorization(accessToken))) {
    headers.set(name, value);
  }
  headers.set(REQUEST_ID_HEADER, correlationId);

  try {
    const upstream = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(8_000),
    });
    const responseHeaders = new Headers({
      "Cache-Control": "private, no-store",
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      // Echoing the ID lets a browser failure be matched to a FastAPI audit row.
      [REQUEST_ID_HEADER]:
        sanitizeRequestId(upstream.headers.get(REQUEST_ID_HEADER)) ?? correlationId,
    });
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { detail: `Could not reach the recommendation API at ${API_BASE_URL}` },
      {
        status: 502,
        headers: {
          "Cache-Control": "private, no-store",
          [REQUEST_ID_HEADER]: correlationId,
        },
      },
    );
  }
}

export function validPositiveId(value: string): boolean {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0;
}
