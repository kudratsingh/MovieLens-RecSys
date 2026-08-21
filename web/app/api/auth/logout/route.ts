import { auth } from "@/auth";
import {
  expireAuthSessionCookies,
  requireBffMutation,
  securityErrorResponse,
} from "@/lib/bff-security";

export async function POST(request: Request) {
  try {
    requireBffMutation(request);
  } catch (error) {
    return securityErrorResponse(error);
  }

  const session = await auth();
  const refreshToken = session?.refreshToken;
  if (refreshToken) {
    const issuer = (
      process.env.KEYCLOAK_INTERNAL_ISSUER ??
      process.env.KEYCLOAK_PUBLIC_ISSUER ??
      "http://localhost:8080/realms/demo"
    ).replace(/\/$/, "");
    await fetch(`${issuer}/protocol/openid-connect/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.KEYCLOAK_CLIENT_ID ?? "movielens-web",
        refresh_token: refreshToken,
      }),
      cache: "no-store",
    }).catch((error) => console.error("Keycloak logout propagation failed", error));
  }

  const response = Response.json({ signed_out: true });
  expireAuthSessionCookies(request, response);
  return response;
}
