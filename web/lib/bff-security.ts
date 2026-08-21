import { createHash, timingSafeEqual } from "node:crypto";

export class BffSecurityError extends Error {
  constructor(
    message: string,
    readonly status: 401 | 403 | 500,
  ) {
    super(message);
  }
}
function cookieValue(request: Request, names: string[]) {
  const cookies = request.headers.get("cookie") ?? "";
  for (const part of cookies.split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const name = part.slice(0, separator).trim();
    if (names.includes(name)) return decodeURIComponent(part.slice(separator + 1).trim());
  }
  return undefined;
}

function constantTimeEqual(left: string, right: string) {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function requireBffMutation(request: Request) {
  const requestOrigin = request.headers.get("origin");
  const expectedOrigin = process.env.APP_ORIGIN ?? new URL(request.url).origin;
  if (!requestOrigin || requestOrigin !== expectedOrigin) {
    throw new BffSecurityError("Mutation origin is not allowed", 403);
  }
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    throw new BffSecurityError("Cross-site mutation is not allowed", 403);
  }

  const secret = process.env.AUTH_SECRET;
  if (!secret) throw new BffSecurityError("Server authentication is not configured", 500);

  const submitted = request.headers.get("x-csrf-token");
  const cookie = cookieValue(request, ["authjs.csrf-token", "__Host-authjs.csrf-token"]);
  if (!submitted || !cookie) throw new BffSecurityError("CSRF token is required", 403);

  const separator = cookie.indexOf("|");
  if (separator < 0) throw new BffSecurityError("CSRF token is invalid", 403);
  const token = cookie.slice(0, separator);
  const hash = cookie.slice(separator + 1);
  const expectedHash = createHash("sha256").update(`${token}${secret}`).digest("hex");
  if (!constantTimeEqual(hash, expectedHash) || !constantTimeEqual(submitted, token)) {
    throw new BffSecurityError("CSRF token is invalid", 403);
  }
}

export function securityErrorResponse(error: unknown) {
  if (error instanceof BffSecurityError) {
    return Response.json({ detail: error.message }, { status: error.status });
  }
  throw error;
}

export function expireAuthSessionCookies(request: Request, response: Response) {
  const requestIsSecure = new URL(request.url).protocol === "https:";
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const separator = part.indexOf("=");
    if (separator < 0) continue;
    const name = part.slice(0, separator).trim();
    if (!name.includes("authjs.session-token")) continue;
    const secure =
      requestIsSecure || name.startsWith("__Secure-") || name.startsWith("__Host-");
    response.headers.append(
      "Set-Cookie",
      `${name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`,
    );
  }
}
