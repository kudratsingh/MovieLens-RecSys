import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import {
  forwardedCredentialRefusal,
  requireBffMutation,
  securityErrorResponse,
} from "@/lib/bff-security";
import { resourceRequestId } from "@/lib/resources/bff";

/**
 * The selected persona's presentation preferences.
 *
 * The read is one persona's own setting under one tenant, so the proxy keeps it
 * `private, no-store` like every other personalized payload. The write goes
 * through the same mutation gate as a movie-state change — Origin, `sec-fetch-
 * site`, and the Auth.js double-submit CSRF token — because it changes what a
 * viewer is shown and a cross-site page must not be able to change it for them.
 */
async function readPreferences(
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
    `/users/${userId}/preferences`,
    {},
    resourceRequestId(request),
  );
}

async function writePreferences(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  try {
    requireBffMutation(request);
  } catch (error) {
    return securityErrorResponse(error);
  }
  const userId = (await context.params).userId;
  if (typeof userId !== "string" || !validPositiveId(userId)) {
    return Response.json({ detail: "Invalid MovieLens user ID" }, { status: 400 });
  }
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }
  const expectedRevision = new URL(request.url).searchParams.get("expected_revision");
  const suffix = expectedRevision
    ? `?expected_revision=${encodeURIComponent(expectedRevision)}`
    : "";
  return proxyRecommendationApi(
    accessToken,
    `/users/${userId}/preferences${suffix}`,
    {
      method: "PUT",
      body: await request.text(),
      headers: new Headers({ "Content-Type": "application/json" }),
    },
    resourceRequestId(request),
  );
}

export const GET = auth(readPreferences);
export const PUT = auth(writePreferences);
