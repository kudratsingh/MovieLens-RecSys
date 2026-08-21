/**
 * Which demo persona a route is exploring as.
 *
 * The signed-in actor and the persona whose data is on screen are different
 * identities, and the routes are role-gated persona mode until a `/me`
 * mapping exists. Resolving the value in one place keeps every route honest
 * about that: an unparseable or absent `user` parameter falls back to the
 * default persona rather than to "the current user", which is a claim these
 * routes are not entitled to make.
 */

export const DEFAULT_DEMO_PERSONA_ID = 900000101;

export function resolveDemoPersonaId(
  value: string | string[] | undefined,
): number {
  const first = Array.isArray(value) ? value[0] : value;
  if (typeof first !== "string" || !/^\d{1,15}$/.test(first)) {
    return DEFAULT_DEMO_PERSONA_ID;
  }
  const parsed = Number(first);
  return Number.isSafeInteger(parsed) && parsed > 0
    ? parsed
    : DEFAULT_DEMO_PERSONA_ID;
}

/**
 * Only a same-origin path back into Browse is honoured. A `returnTo` arrives
 * from a link the browser controls, so anything else is discarded rather than
 * followed.
 */
export function safeBrowseReturnHref(
  value: string | string[] | undefined,
  fallback: string,
  browsePath = "/browse",
): string {
  const first = Array.isArray(value) ? value[0] : value;
  if (typeof first !== "string") return fallback;
  if (first !== browsePath && !first.startsWith(`${browsePath}?`)) return fallback;
  // A protocol-relative or backslash-prefixed value would leave the origin.
  if (first.includes("//") || first.includes("\\")) return fallback;
  return first;
}
