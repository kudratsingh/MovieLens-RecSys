/**
 * Structural view of the Auth.js session the BFF needs. A real `Session`
 * satisfies it, and so does a test double, which keeps the token rules
 * testable without constructing a whole next-auth session.
 */
export type ApiSession =
  | { accessToken?: string; error?: string }
  | null
  | undefined;

export function requireApiAccessToken(session: ApiSession) {
  if (!session?.accessToken || session.error) {
    return undefined;
  }
  return session.accessToken;
}
export function apiAuthorization(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}
