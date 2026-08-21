import NextAuth, { customFetch, type NextAuthConfig } from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

import {
  accessTokenClaims,
  publicIssuer,
  refreshAccessToken,
  rewriteOidcUrl,
  type AuthToken,
} from "@/lib/auth-token";

async function keycloakFetch(input: RequestInfo | URL, init?: RequestInit) {
  if (input instanceof Request) {
    return fetch(new Request(rewriteOidcUrl(input.url), input), init);
  }
  return fetch(rewriteOidcUrl(input), init);
}

export const authConfig: NextAuthConfig = {
  trustHost: true,
  session: { strategy: "jwt", maxAge: 10 * 60 * 60 },
  providers: [
    Keycloak({
      clientId: process.env.KEYCLOAK_CLIENT_ID ?? "movielens-web",
      issuer: publicIssuer(),
      client: { token_endpoint_auth_method: "none" },
      authorization: { params: { scope: "openid profile email" } },
      checks: ["pkce", "state", "nonce"],
      [customFetch]: keycloakFetch,
    }),
  ],
  callbacks: {
    async jwt({ token, account }) {
      const current = token as AuthToken;
      if (account) {
        const accessToken = account.access_token;
        return {
          ...current,
          ...accessTokenClaims(accessToken),
          accessToken,
          accessTokenExpiresAt:
            typeof account.expires_at === "number"
              ? account.expires_at * 1_000
              : Date.now() + 5 * 60 * 1_000,
          refreshToken: account.refresh_token,
          idToken: account.id_token,
          error: undefined,
        };
      }

      return refreshAccessToken(current);
    },
    async session({ session, token }) {
      const current = token as AuthToken;
      session.accessToken = current.accessToken;
      session.refreshToken = current.refreshToken;
      session.idToken = current.idToken;
      session.accessTokenExpiresAt = current.accessTokenExpiresAt;
      session.authorizedParty = current.authorizedParty;
      session.roles = current.roles ?? [];
      session.error = current.error;
      return session;
    },
  },
};

export const { auth, handlers, signIn, signOut } = NextAuth(authConfig);
