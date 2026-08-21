import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { apiAuthorization, requireApiAccessToken } from "@/lib/bff-auth";
import { requireBffMutation, securityErrorResponse } from "@/lib/bff-security";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

function requestHeaders(accessToken: string) {
  return {
    "Content-Type": "application/json",
    ...apiAuthorization(accessToken),
  };
}

async function rateMovie(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  try {
    requireBffMutation(request);
  } catch (error) {
    return securityErrorResponse(error);
  }

  const userId = (await context.params).userId;
  if (typeof userId !== "string") {
    return Response.json({ detail: "Invalid rating request" }, { status: 400 });
  }
  const body = (await request.json().catch(() => null)) as
    | { movie_id?: unknown; rating?: unknown }
    | null;
  if (!/^\d+$/.test(userId) || !body || typeof body.movie_id !== "number") {
    return Response.json({ detail: "Invalid rating request" }, { status: 400 });
  }
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }
  try {
    const response = await fetch(
      `${API_BASE_URL}/users/${userId}/ratings/${body.movie_id}`,
      {
        method: "PUT",
        headers: requestHeaders(accessToken),
        body: JSON.stringify({ rating: body.rating }),
      },
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Could not reach the recommendation API" }, { status: 502 });
  }
}

async function resetRatings(
  request: NextAuthRequest,
  context: { params: Promise<Record<string, string | string[] | undefined>> },
) {
  try {
    requireBffMutation(request);
  } catch (error) {
    return securityErrorResponse(error);
  }

  const userId = (await context.params).userId;
  if (typeof userId !== "string") {
    return Response.json({ detail: "Invalid MovieLens user ID" }, { status: 400 });
  }
  if (!/^\d+$/.test(userId)) {
    return Response.json({ detail: "Invalid MovieLens user ID" }, { status: 400 });
  }
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }
  try {
    const response = await fetch(`${API_BASE_URL}/users/${userId}/ratings`, {
      method: "DELETE",
      headers: requestHeaders(accessToken),
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Could not reach the recommendation API" }, { status: 502 });
  }
}

export const POST = auth(rateMovie);
export const DELETE = auth(resetRatings);
