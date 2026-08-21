import "server-only";

/**
 * Server-side reads for the Quick Picks route.
 *
 * These sit on the 5A live-resource boundary rather than beside it: the access
 * token comes from the Auth.js session, the timeouts and validators belong to
 * the resource registry, and a failure produces a resource failure state that
 * the route renders honestly. No fixture is reachable from this module.
 *
 * The audit read is deliberately sequential rather than parallel — the row that
 * explains this queue does not exist until the recommendations request has
 * committed its prediction audit.
 */

import { auth } from "@/auth";
import { QUICK_PICK_AUDIT_LOOKBACK, QUICK_PICK_QUEUE_LIMIT } from "@/lib/quick-picks/contract";
import { EMPTY_EVIDENCE, quickPickEvidence } from "@/lib/quick-picks/evidence";
import type { QuickPickQueuePayload } from "@/lib/quick-picks/transport";
import {
  loadRecommendationAudits,
  loadRecommendations,
} from "@/lib/resources/server";
import { hasResourceData } from "@/lib/resources/state";

export async function loadQuickPickQueue(
  userId: number,
): Promise<QuickPickQueuePayload> {
  const session = await auth();
  const queue = await loadRecommendations(userId, {
    session,
    limit: QUICK_PICK_QUEUE_LIMIT,
  });
  if (!hasResourceData(queue)) return { queue, evidence: EMPTY_EVIDENCE };

  const audits = await loadRecommendationAudits(userId, {
    session,
    limit: QUICK_PICK_AUDIT_LOOKBACK,
  });
  return {
    queue,
    // Evidence is optional context. A failed audit read costs the "why this?"
    // sentence and nothing else.
    evidence: hasResourceData(audits)
      ? quickPickEvidence(audits.data, queue.requestId)
      : EMPTY_EVIDENCE,
  };
}
