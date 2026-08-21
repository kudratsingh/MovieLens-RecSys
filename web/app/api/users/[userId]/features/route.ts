import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { forwardedCredentialRefusal } from "@/lib/bff-security";
import { resourceRequestId } from "@/lib/resources/bff";

/**
 * Online feature values for the advanced disclosure. FastAPI resolves the
 * tenant from the token and applies its own persona authorization; this route
 * adds the same credential refusal and correlation ID as every other read.
 */
async function features(
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
  return proxyRecommendationApi(
    accessToken,
    `/users/${userId}/features`,
    {},
    resourceRequestId(request),
  );
}

export const GET = auth(features);
