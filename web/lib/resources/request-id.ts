export const REQUEST_ID_HEADER = "X-Request-ID";

/**
 * Request IDs travel between three systems and end up in FastAPI's prediction
 * audit rows, so only an opaque, log-safe token is accepted from anyone else.
 * Anything with whitespace, control characters, or unusual length is treated
 * as absent rather than sanitized into something subtly different.
 */
const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;

export function sanitizeRequestId(
  value: string | null | undefined,
): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return REQUEST_ID_PATTERN.test(trimmed) ? trimmed : undefined;
}

export function newRequestId(): string {
  const source = globalThis.crypto;
  if (source && typeof source.randomUUID === "function") {
    return source.randomUUID();
  }
  // jsdom and older runtimes without a secure-context UUID source still need a
  // correlatable ID; uniqueness matters here, unpredictability does not.
  const random = Math.random().toString(16).slice(2).padEnd(13, "0");
  return `rid-${Date.now().toString(16)}-${random}`;
}
