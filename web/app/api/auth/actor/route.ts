import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import type { CurrentActorResponse } from "@/lib/api";
import { apiAuthorization, requireApiAccessToken } from "@/lib/bff-auth";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

async function actor(request: NextAuthRequest) {
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json({ detail: "Your session has expired. Sign in again." }, { status: 401 });
  }

  try {
    const response = await fetch(`${API_BASE_URL}/whoami`, {
      cache: "no-store",
      headers: apiAuthorization(accessToken),
    });
    const body = (await response.json().catch(() => null)) as
      | CurrentActorResponse
      | { detail?: string }
      | null;
    if (!response.ok) {
      return Response.json(
        { detail: body && "detail" in body ? body.detail : "Actor lookup failed" },
        { status: response.status },
      );
    }
    return Response.json(body as CurrentActorResponse);
  } catch {
    return Response.json({ detail: "Could not reach the recommendation API" }, { status: 502 });
  }
}

export const GET = auth(actor);
