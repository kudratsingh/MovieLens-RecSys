import "server-only";

import { apiAuthorization } from "@/lib/bff-auth";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

export async function proxyRecommendationApi(
  accessToken: string,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  for (const [name, value] of Object.entries(apiAuthorization(accessToken))) {
    headers.set(name, value);
  }

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
    });
    const requestId = upstream.headers.get("x-request-id");
    if (requestId) responseHeaders.set("X-Request-ID", requestId);
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { detail: `Could not reach the recommendation API at ${API_BASE_URL}` },
      { status: 502, headers: { "Cache-Control": "private, no-store" } },
    );
  }
}

export function validPositiveId(value: string): boolean {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0;
}
