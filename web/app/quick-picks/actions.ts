"use server";

/**
 * Server actions for the Quick Picks route.
 *
 * The queue refetch and the seed-title lookup are reads the browser cannot make
 * for itself — the API access token lives in the server session. They are
 * exposed as bound server actions rather than as new BFF routes because these
 * reads belong to this route: Discover owns the shape of a shared
 * recommendations route, and claiming that file here would put two owners on
 * one contract.
 *
 * Both delegate to the 5A boundary, so authorization, timeouts, request-ID
 * propagation, and payload validation are the same as every other live read.
 */

import { loadQuickPickQueue, loadQuickPickSeedTitle } from "@/lib/quick-picks/server";
import type { QuickPickQueuePayload } from "@/lib/quick-picks/transport";

export async function refreshQuickPickQueue(
  userId: number,
): Promise<QuickPickQueuePayload> {
  return loadQuickPickQueue(userId);
}

export async function resolveQuickPickSeedTitle(
  userId: number,
  movieId: number,
): Promise<string | null> {
  return loadQuickPickSeedTitle(userId, movieId);
}
