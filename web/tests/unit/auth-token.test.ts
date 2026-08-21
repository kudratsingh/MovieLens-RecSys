import { afterEach, describe, expect, it, vi } from "vitest";

import {
  accessTokenClaims,
  performRefresh,
  rewriteOidcUrl,
} from "../../lib/auth-token";

function accessToken(payload: Record<string, unknown>) {
  return `header.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.signature`;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("Keycloak browser session", () => {
  it("rewrites only trusted public-issuer traffic to the internal Compose origin", () => {
    vi.stubEnv("KEYCLOAK_PUBLIC_ISSUER", "http://localhost:8080/realms/demo");
    vi.stubEnv("KEYCLOAK_INTERNAL_ISSUER", "http://keycloak:8080/realms/demo");

    expect(
      rewriteOidcUrl("http://localhost:8080/realms/demo/protocol/openid-connect/token").href,
    ).toBe("http://keycloak:8080/realms/demo/protocol/openid-connect/token");
    expect(rewriteOidcUrl("https://example.com/data").href).toBe("https://example.com/data");
  });

  it("extracts authorization metadata without treating the unverified payload as identity proof", () => {
    expect(
      accessTokenClaims(
        accessToken({ azp: "movielens-web", realm_access: { roles: ["user", 7] } }),
      ),
    ).toEqual({ authorizedParty: "movielens-web", roles: ["user"] });
  });

  it("rotates an expired access token and preserves a reusable refresh token", async () => {
    vi.stubEnv("KEYCLOAK_INTERNAL_ISSUER", "http://keycloak:8080/realms/demo");
    vi.stubEnv("KEYCLOAK_CLIENT_ID", "movielens-web");
    const refreshedAccessToken = accessToken({
      azp: "movielens-web",
      realm_access: { roles: ["user", "demo-impersonator"] },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json({ access_token: refreshedAccessToken, expires_in: 300 }),
      ),
    );

    const refreshed = await performRefresh({ refreshToken: "refresh-1", sub: "demo" });

    expect(refreshed.accessToken).toBe(refreshedAccessToken);
    expect(refreshed.refreshToken).toBe("refresh-1");
    expect(refreshed.roles).toEqual(["user", "demo-impersonator"]);
    expect(refreshed.error).toBeUndefined();
  });

  it("marks the session expired when Keycloak rejects refresh", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(performRefresh({ refreshToken: "expired", sub: "demo" })).resolves.toMatchObject({
      error: "RefreshAccessTokenError",
    });
  });
});
