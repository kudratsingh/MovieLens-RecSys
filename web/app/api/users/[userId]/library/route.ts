import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { forwardedCredentialRefusal } from "@/lib/bff-security";
import { resourceRequestId } from "@/lib/resources/bff";

async function library(
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
  const source = new URL(request.url).searchParams;
  const target = new URLSearchParams();
  for (const name of [
    "tab",
    "sort",
    "limit",
    "cursor",
    "q",
    "genre",
    "year_from",
    "year_to",
  ]) {
    const value = source.get(name);
    if (value) target.set(name, value);
  }
  const suffix = target.size ? `?${target.toString()}` : "";
  return proxyRecommendationApi(
    accessToken,
    `/users/${userId}/library${suffix}`,
    {},
    resourceRequestId(request),
  );
}

export const GET = auth(library);
