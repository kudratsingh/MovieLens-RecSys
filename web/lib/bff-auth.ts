import type { Session } from "next-auth";

export function requireApiAccessToken(session: Session | null) {
  if (!session?.accessToken || session.error) {
    return undefined;
  }
  return session.accessToken;
}
export function apiAuthorization(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}
