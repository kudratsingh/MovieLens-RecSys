"use server";

/**
 * Server actions for the Quick Picks route.
 *
 * Refetching the queue is one read the browser cannot make for itself: the
 * recommendations response and the prediction audit that explains it have to be
 * sequenced server-side, because the audit row does not exist until the
 * recommendations request has committed it. Discover's shared BFF route serves
 * recommendations alone, so it is not a drop-in for that pair.
 *
 * It delegates to the 5A boundary, so authorization, timeouts, request-ID
 * propagation, and payload validation are the same as every other live read.
 */

import { loadQuickPickQueue } from "@/lib/quick-picks/server";
import type { QuickPickQueuePayload } from "@/lib/quick-picks/transport";

export async function refreshQuickPickQueue(
  userId: number,
): Promise<QuickPickQueuePayload> {
  return loadQuickPickQueue(userId);
}
