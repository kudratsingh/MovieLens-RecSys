import { createHash } from "node:crypto";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BffSecurityError,
  expireAuthSessionCookies,
  requireBffMutation,
} from "../../lib/bff-security";

const ORIGIN = "http://localhost:3001";

function mutationRequest({ origin = ORIGIN, submitted = "csrf-token" } = {}) {
  const secret = "unit-test-secret-with-enough-entropy";
  vi.stubEnv("AUTH_SECRET", secret);
  vi.stubEnv("APP_ORIGIN", ORIGIN);
  const hash = createHash("sha256").update(`csrf-token${secret}`).digest("hex");
  return new Request(`${ORIGIN}/api/users/1/ratings`, {
    method: "POST",
    headers: {
      cookie: `authjs.csrf-token=${encodeURIComponent(`csrf-token|${hash}`)}`,
      origin,
      "x-csrf-token": submitted,
    },
  });
}

afterEach(() => vi.unstubAllEnvs());

describe("requireBffMutation", () => {
  it("accepts a same-origin request with Auth.js double-submit CSRF proof", () => {
    expect(() => requireBffMutation(mutationRequest())).not.toThrow();
  });

  it("rejects a cross-origin mutation", () => {
    expect(() =>
      requireBffMutation(mutationRequest({ origin: "https://attacker.example" })),
    ).toThrowError(BffSecurityError);
  });

  it("rejects a forged CSRF header", () => {
    expect(() => requireBffMutation(mutationRequest({ submitted: "forged" }))).toThrowError(
      /CSRF token is invalid/,
    );
  });

  it("expires every chunk of a secure Auth.js session behind a TLS proxy", () => {
    const request = new Request("http://web:3001/api/auth/logout", {
      headers: {
        cookie:
          "__Secure-authjs.session-token.0=first; theme=dark; __Secure-authjs.session-token.1=second",
      },
    });
    const response = Response.json({ signed_out: true });

    expireAuthSessionCookies(request, response);

    const expired = response.headers.getSetCookie();
    expect(expired).toHaveLength(2);
    expect(expired).toEqual(
      expect.arrayContaining([
        expect.stringContaining("__Secure-authjs.session-token.0=;"),
        expect.stringContaining("__Secure-authjs.session-token.1=;"),
      ]),
    );
    expect(expired.every((cookie) => cookie.includes("; Secure"))).toBe(true);
    expect(expired.some((cookie) => cookie.startsWith("theme="))).toBe(false);
  });
});
