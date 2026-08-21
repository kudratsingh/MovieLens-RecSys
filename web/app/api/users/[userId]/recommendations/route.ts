import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { forwardedCredentialRefusal } from "@/lib/bff-security";
import { resourceRequestId } from "@/lib/resources/bff";

/**
 * Discover re-reads recommendations from the browser after a committed
 * feedback action, so the refreshed-copy promise can wait on a real request
 * rather than a timer. The payload is one persona's ranked set under one
 * tenant, so `proxyRecommendationApi` keeps it `private, no-store`.
 */
async function recommendations(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  const refusal = forwardedCredentialRefusal(request);
  if (refusal) return refusal;
  const userId = (await context.params).userId;
  if (typeof userId !== "string" || !validPositiveId(userId)) {
    return Response.json({ detail: "Invalid MovieLens user ID" }, { status: 400 });
  }
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }
  const limit = new URL(request.url).searchParams.get("limit");
  const suffix = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  return proxyRecommendationApi(
    accessToken,
    `/users/${userId}/recommendations${suffix}`,
    {},
    resourceRequestId(request),
  );
}

export const GET = auth(recommendations);
