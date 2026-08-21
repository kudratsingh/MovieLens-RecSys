import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";

async function detail(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  const { userId, movieId } = await context.params;
  if (
    typeof userId !== "string" ||
    typeof movieId !== "string" ||
    !validPositiveId(userId) ||
    !validPositiveId(movieId)
  ) {
    return Response.json({ detail: "Invalid movie detail request" }, { status: 400 });
  }
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }
  return proxyRecommendationApi(accessToken, `/users/${userId}/movies/${movieId}`);
}

export const GET = auth(detail);
