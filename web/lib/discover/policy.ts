/**
 * How Discover is allowed to describe the way a recommendation set was served.
 *
 * The serving policy is not a UI preference. FastAPI reports it per response,
 * and the route's copy has to follow it exactly: a popularity fallback may
 * never be dressed up as personalization, and learned copy may only appear
 * when the response says learned serving happened.
 *
 * Bundle 6 made `serving_policy` a required field: an explicit `learned`
 * boolean, the persona's signal count against the router's threshold, the
 * recorded reason, and the scale the item scores are on. That envelope is the
 * authority. The flat `policy` string is still read as a fallback, because a
 * proxy or an older deployment can still answer without the envelope and
 * inferring from the string is better than showing nothing — but the inference
 * never overrides a reported value.
 */

import type { RecommendationResponse } from "@/lib/api";
import { isRecord } from "@/lib/resources/validate";

/**
 * The router's documented cold-start boundary (ADR 0011, ADR 0012 §6). Used
 * only when a response does not report its own threshold.
 */
export const LEARNED_SERVING_SIGNAL_THRESHOLD = 5;

/**
 * A learned response reports `<candidate policy>+lightgbm`; the fallback
 * reports `popularity`. Matching on the ranker stage rather than an exact
 * string means swapping the candidate generator cannot silently demote a
 * learned response to fallback copy. This is the pre-envelope path only.
 */
const LEARNED_RANKER_STAGE = "lightgbm";

export type ServingPolicyKind = "learned" | "fallback";

/**
 * Presentation, not contract: named `…Copy` so it cannot be confused with the
 * generated `ServingPolicy` API type once the envelope lands in `lib/api.ts`.
 */
export type ServingPolicyCopy = {
  kind: ServingPolicyKind;
  /** Exactly what the response reported. Never normalized for display. */
  raw: string;
  /** The response's own reason, when it sent one. Quoted, never paraphrased. */
  reason: string | null;
  /** The scale item scores are on, when the response named it. */
  scoreScale: string | null;
  /** Short label shown beside the primary movie. */
  label: string;
  /** One sentence that stays inside what the response actually claimed. */
  summary: string;
  /** Extra sentence shown for a fallback, as specific as the response allows. */
  note: string;
  /** True when the response stated `learned` rather than us inferring it. */
  reported: boolean;
};

export const FALLBACK_POLICY_LABEL = "Popular while we learn";
export const LEARNED_POLICY_LABEL = "Ranked by the learned model";

/**
 * Said next to the fallback label when the response carries no signal count.
 * It describes the router's rule and points at the audit for this response's
 * recorded reason instead of guessing between cold start, an unavailable model
 * server, and an empty learned result.
 */
export const FALLBACK_ROUTING_NOTE =
  `The deployed router only attempts learned serving once a persona has ` +
  `${LEARNED_SERVING_SIGNAL_THRESHOLD} or more watched titles. The reason recorded for ` +
  `this exact response is in its prediction audit.`;

/**
 * Read structurally rather than through the generated type so a response that
 * arrives without the envelope, or with half of one, degrades to the string
 * path instead of throwing at the point of use.
 */
export type ServingPolicyEnvelope = {
  name: string;
  learned: boolean;
  positive_signal_count: number | null;
  threshold: number | null;
  reason: string | null;
  /** Names what the item scores are, so nothing has to guess they are odds. */
  score_scale: string | null;
};

export function readServingPolicyEnvelope(
  response: RecommendationResponse,
): ServingPolicyEnvelope | null {
  const candidate = (response as { serving_policy?: unknown }).serving_policy;
  if (!isRecord(candidate)) return null;
  if (typeof candidate.name !== "string" || typeof candidate.learned !== "boolean") {
    return null;
  }
  return {
    name: candidate.name,
    learned: candidate.learned,
    positive_signal_count:
      typeof candidate.positive_signal_count === "number"
        ? candidate.positive_signal_count
        : null,
    threshold: typeof candidate.threshold === "number" ? candidate.threshold : null,
    reason: typeof candidate.reason === "string" ? candidate.reason : null,
    score_scale:
      typeof candidate.score_scale === "string" ? candidate.score_scale : null,
  };
}

function namesLearnedRanker(policy: string): boolean {
  return policy.toLowerCase().includes(LEARNED_RANKER_STAGE);
}

function fallbackNote(envelope: ServingPolicyEnvelope | null): string {
  if (!envelope) return FALLBACK_ROUTING_NOTE;
  const { positive_signal_count: signals, threshold } = envelope;
  if (signals === null || threshold === null) {
    // No counts to quote, so the routing rule is all that can honestly be said.
    // The response's own reason is surfaced as evidence rather than paraphrased.
    return FALLBACK_ROUTING_NOTE;
  }
  return (
    `This persona has ${signals} of the ${threshold} watched signals the deployed ` +
    `router needs before it attempts learned serving.`
  );
}

/** Classifies a flat policy string. Used when a response carries no envelope. */
export function servingPolicyCopy(policy: string): ServingPolicyCopy {
  const raw = policy.trim();
  return namesLearnedRanker(raw)
    ? learnedCopy(raw, null, false)
    : fallbackCopy(raw, null, false);
}

function learnedCopy(
  raw: string,
  envelope: ServingPolicyEnvelope | null,
  reported: boolean,
): ServingPolicyCopy {
  return {
    kind: "learned",
    raw,
    reason: envelope?.reason ?? null,
    scoreScale: envelope?.score_scale ?? null,
    label: LEARNED_POLICY_LABEL,
    summary:
      "This response reported learned serving: retrieved candidates re-ranked by the deployed LightGBM ranker.",
    note: "",
    reported,
  };
}

function fallbackCopy(
  raw: string,
  envelope: ServingPolicyEnvelope | null,
  reported: boolean,
): ServingPolicyCopy {
  return {
    kind: "fallback",
    raw,
    reason: envelope?.reason ?? null,
    scoreScale: envelope?.score_scale ?? null,
    label: FALLBACK_POLICY_LABEL,
    summary:
      "This response did not report learned serving, so the list is the tenant's popularity fallback.",
    note: fallbackNote(envelope),
    reported,
  };
}

export function describeServingPolicy(
  response: RecommendationResponse,
): ServingPolicyCopy {
  const envelope = readServingPolicyEnvelope(response);
  if (!envelope) return servingPolicyCopy(response.policy);
  const raw = envelope.name.trim() || response.policy.trim();
  return envelope.learned
    ? learnedCopy(raw, envelope, true)
    : fallbackCopy(raw, envelope, true);
}
