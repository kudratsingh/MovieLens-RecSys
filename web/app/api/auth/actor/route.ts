import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { forwardedCredentialRefusal } from "@/lib/bff-security";
import { PRIVATE_NO_STORE, resourceRequestId } from "@/lib/resources/bff";
import { REQUEST_ID_HEADER } from "@/lib/resources/request-id";

/**
 * Who the browser is currently authenticated as, according to the API rather
 * than to the session cookie.
 *
 * It answers with the caller's tenant, subject, and realm roles — as
 * personalized as a payload gets — and used to do so with no `Cache-Control`
 * and no correlation ID, against the rule `lib/resources/bff.ts` states for
 * every BFF read. `proxyRecommendationApi` carries both, and passes the
 * upstream body through unchanged so the role checks that read this keep
 * seeing exactly what `/whoami` said.
 */
async function actor(request: NextAuthRequest) {
  const refusal = forwardedCredentialRefusal(request);
  if (refusal) return refusal;

  const requestId = resourceRequestId(request);
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json(
      { detail: "Your session has expired. Sign in again." },
      {
        status: 401,
        headers: { "Cache-Control": PRIVATE_NO_STORE, [REQUEST_ID_HEADER]: requestId },
      },
    );
  }

  return proxyRecommendationApi(accessToken, "/whoami", {}, requestId);
}

export const GET = auth(actor);
