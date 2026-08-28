import type { NextAuthRequest } from "next-auth";

import { auth } from "@/auth";
import { proxyRecommendationApi } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { forwardedCredentialRefusal } from "@/lib/bff-security";
import { PRIVATE_NO_STORE, resourceRequestId } from "@/lib/resources/bff";
import { REQUEST_ID_HEADER } from "@/lib/resources/request-id";

/**
 * The demo persona directory, as the browser sees it.
 *
 * It hand-rolled its own `fetch` and so answered without the two headers
 * `lib/resources/bff.ts` states every BFF read owes: `private, no-store`, on a
 * payload that is one tenant's persona list, and an `X-Request-ID` a browser
 * failure can be matched to a FastAPI audit row by. `proxyRecommendationApi`
 * is where both live, and routing through it also drops any credential the
 * caller tried to contribute.
 */
async function personas(request: NextAuthRequest) {
  const refusal = forwardedCredentialRefusal(request);
  if (refusal) return refusal;

  const requestId = resourceRequestId(request);
  const accessToken = requireApiAccessToken(request.auth);
  if (!accessToken) {
    return Response.json(
      { detail: "Sign in to choose a demo persona" },
      {
        status: 401,
        headers: { "Cache-Control": PRIVATE_NO_STORE, [REQUEST_ID_HEADER]: requestId },
      },
    );
  }

  return proxyRecommendationApi(accessToken, "/personas", {}, requestId);
}

export const GET = auth(personas);
