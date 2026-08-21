const REFRESH_SKEW_MS = 30_000;
const inflightRefreshes = new Map<string, Promise<AuthToken>>();

export type AuthToken = {
  accessToken?: string;
  accessTokenExpiresAt?: number;
  authorizedParty?: string;
  error?: "RefreshAccessTokenError";
  idToken?: string;
  name?: string | null;
  email?: string | null;
  picture?: string | null;
  refreshToken?: string;
  roles?: string[];
  sub?: string;
  [key: string]: unknown;
};

export function publicIssuer() {
  return (
    process.env.KEYCLOAK_PUBLIC_ISSUER ??
    "http://localhost:8080/realms/demo"
  ).replace(/\/$/, "");
}
export function internalIssuer() {
  return (process.env.KEYCLOAK_INTERNAL_ISSUER ?? publicIssuer()).replace(/\/$/, "");
}

export function rewriteOidcUrl(input: string | URL) {
  const requested = new URL(input);
  const publicUrl = new URL(publicIssuer());
  if (
    requested.origin !== publicUrl.origin ||
    !requested.pathname.startsWith(`${publicUrl.pathname}/`)
  ) {
    return requested;
  }

  const internalUrl = new URL(internalIssuer());
  requested.protocol = internalUrl.protocol;
  requested.host = internalUrl.host;
  requested.pathname = `${internalUrl.pathname}${requested.pathname.slice(
    publicUrl.pathname.length,
  )}`;
  return requested;
}

export function accessTokenClaims(accessToken: string | undefined) {
  if (!accessToken) return {};
  try {
    const encoded = accessToken.split(".")[1];
    if (!encoded) return {};
    const claims = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8")) as {
      azp?: unknown;
      realm_access?: { roles?: unknown };
    };
    const roles = Array.isArray(claims.realm_access?.roles)
      ? claims.realm_access.roles.filter((role): role is string => typeof role === "string")
      : [];
    return {
      authorizedParty: typeof claims.azp === "string" ? claims.azp : undefined,
      roles,
    };
  } catch {
    return {};
  }
}

export async function performRefresh(token: AuthToken): Promise<AuthToken> {
  if (!token.refreshToken) return { ...token, error: "RefreshAccessTokenError" };

  try {
    const response = await fetch(`${internalIssuer()}/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.KEYCLOAK_CLIENT_ID ?? "movielens-web",
        grant_type: "refresh_token",
        refresh_token: token.refreshToken,
      }),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`Keycloak refresh returned ${response.status}`);

    const refreshed = (await response.json()) as {
      access_token?: string;
      expires_in?: number;
      id_token?: string;
      refresh_token?: string;
    };
    if (!refreshed.access_token || !refreshed.expires_in) {
      throw new Error("Keycloak refresh response is incomplete");
    }
    return {
      ...token,
      ...accessTokenClaims(refreshed.access_token),
      accessToken: refreshed.access_token,
      accessTokenExpiresAt: Date.now() + refreshed.expires_in * 1_000,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      idToken: refreshed.id_token ?? token.idToken,
      error: undefined,
    };
  } catch (error) {
    console.error("Keycloak access-token refresh failed", error);
    return { ...token, error: "RefreshAccessTokenError" };
  }
}

export async function refreshAccessToken(token: AuthToken) {
  if (
    token.accessToken &&
    token.accessTokenExpiresAt &&
    Date.now() < token.accessTokenExpiresAt - REFRESH_SKEW_MS
  ) {
    return token;
  }

  const key = token.refreshToken ?? token.sub ?? "anonymous";
  const current = inflightRefreshes.get(key);
  if (current) return current;

  const refresh = performRefresh(token).finally(() => inflightRefreshes.delete(key));
  inflightRefreshes.set(key, refresh);
  return refresh;
}
