import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import type { PersonaResponse } from "@/lib/api";
import { apiAuthorization, requireApiAccessToken } from "@/lib/bff-auth";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

async function personas(request: NextAuthRequest) {
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Sign in to choose a demo persona" }, { status: 401 });
  }

  try {
    const response = await fetch(`${API_BASE_URL}/personas`, {
      cache: "no-store",
      headers: apiAuthorization(accessToken),
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      return Response.json(
        { detail: body?.detail ?? `Recommendation API returned ${response.status}` },
        { status: response.status },
      );
    }
    return Response.json((await response.json()) as PersonaResponse);
  } catch {
    return Response.json(
      {
        detail: `Could not reach the recommendation API at ${API_BASE_URL}. Start it with make serve.`,
      },
      { status: 502 },
    );
  }
}

export const GET = auth(personas);
