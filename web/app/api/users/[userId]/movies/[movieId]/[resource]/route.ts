import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { requireBffMutation, securityErrorResponse } from "@/lib/bff-security";

const RESOURCES = new Set(["watched", "rating", "watchlist", "dismissal"]);

async function mutate(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
  method: "PUT" | "DELETE",
) {
  try {
    requireBffMutation(request);
  } catch (error) {
    return securityErrorResponse(error);
  }
  const { userId, movieId, resource } = await context.params;
  if (
    typeof userId !== "string" ||
    typeof movieId !== "string" ||
    typeof resource !== "string" ||
    !validPositiveId(userId) ||
    !validPositiveId(movieId) ||
    !RESOURCES.has(resource)
  ) {
    return Response.json({ detail: "Invalid feedback resource" }, { status: 400 });
  }
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }
  const expectedRevision = new URL(request.url).searchParams.get("expected_revision");
  const suffix = expectedRevision
    ? `?expected_revision=${encodeURIComponent(expectedRevision)}`
    : "";
  const body = method === "PUT" && resource === "rating" ? await request.text() : undefined;
  const headers = new Headers();
  const idempotencyKey = request.headers.get("idempotency-key");
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  return proxyRecommendationApi(
    accessToken,
    `/users/${userId}/movies/${movieId}/${resource}${suffix}`,
    { method, body, headers },
  );
}

async function put(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  return mutate(request, context, "PUT");
}

async function remove(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  return mutate(request, context, "DELETE");
}

export const PUT = auth(put);
export const DELETE = auth(remove);
