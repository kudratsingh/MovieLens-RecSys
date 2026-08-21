import "server-only";

/**
 * The selected persona's display name for the shell.
 *
 * The shell must not let a MovieLens persona read as the signed-in human's own
 * account, so it needs a real name to put behind "Exploring as". This is
 * chrome rather than a product region: if the lookup fails, the route still
 * renders and falls back to the numeric persona rather than showing an error.
 */

import type { PersonaResponse } from "@/lib/api";
import { proxyRecommendationApi } from "@/lib/backend";

export function fallbackPersonaName(userId: number): string {
  return `Persona ${userId}`;
}

export async function personaDisplayName(
  accessToken: string | undefined,
  userId: number,
  requestId?: string | null,
): Promise<string> {
  if (!accessToken) return fallbackPersonaName(userId);
  try {
    const response = await proxyRecommendationApi(
      accessToken,
      "/personas",
      {},
      requestId,
    );
    if (!response.ok) return fallbackPersonaName(userId);
    const payload = (await response.json()) as PersonaResponse;
    const selected = payload.items?.find((item) => item.user_id === userId);
    return selected?.display_name ?? fallbackPersonaName(userId);
  } catch {
    return fallbackPersonaName(userId);
  }
}
