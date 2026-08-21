import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { forwardedCredentialRefusal } from "@/lib/bff-security";
import { resourceRequestId } from "@/lib/resources/bff";

async function catalog(
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
  const incoming = new URL(request.url);
  const allowed = new URLSearchParams();
  for (const name of [
    "q",
    "genre",
    "year_from",
    "year_to",
    "sort",
    "limit",
    "cursor",
  ]) {
    const value = incoming.searchParams.get(name);
    if (value) allowed.set(name, value);
  }
  const suffix = allowed.size ? `?${allowed.toString()}` : "";
  return proxyRecommendationApi(
    accessToken,
    `/users/${userId}/catalog${suffix}`,
    {},
    resourceRequestId(request),
  );
}

export const GET = auth(catalog);
