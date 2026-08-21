import "server-only";

/**
 * The selected persona's display name for the shell.
 *
 * The shell must not let a MovieLens persona read as the signed-in human's own
 * account, so it needs a real name to put behind "Exploring as". This is
 * chrome rather than a product region: if the lookup fails, the route still
 * renders and falls back to the numeric persona rather than showing an error.
 *
 * Two entry points, because the routes need different things from the same
 * read. Most of them only want something to print, and `personaDisplayName`
 * gives them that. Library also has to know whether the name is real, because
 * it renders a disclaimer that reads differently for a resolved persona than
 * for an ID it could not look up — so it takes `resolvePersonaName` and decides
 * for itself.
 */

import type { PersonaResponse } from "@/lib/api";
import { proxyRecommendationApi } from "@/lib/backend";

export function fallbackPersonaName(userId: number): string {
  return `Persona ${userId}`;
}

/** The persona's display name, or `null` if the directory could not answer. */
export async function resolvePersonaName(
  accessToken: string | undefined,
  userId: number,
  requestId?: string | null,
): Promise<string | null> {
  if (!accessToken) return null;
  try {
    const response = await proxyRecommendationApi(
      accessToken,
      "/personas",
      {},
      requestId,
    );
    if (!response.ok) return null;
    const payload = (await response.json()) as PersonaResponse;
    return payload.items?.find((item) => item.user_id === userId)?.display_name ?? null;
  } catch {
    return null;
  }
}

export async function personaDisplayName(
  accessToken: string | undefined,
  userId: number,
  requestId?: string | null,
): Promise<string> {
  return (
    (await resolvePersonaName(accessToken, userId, requestId)) ??
    fallbackPersonaName(userId)
  );
}
