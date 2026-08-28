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
/**
 * The fallback label for a persona that is *not* cold.
 *
 * "Popular while we learn" reads as cold start, and since PR #64 a warm persona
 * can be routed to the fallback too — `unseeded-retrieval` returns
 * `learned: false` with a signal count well past the threshold. Labelling that
 * as "while we learn" tells the viewer the system is still gathering signals
 * when it has plenty; this label says what happened without inventing why.
 */
export const POPULARITY_FALLBACK_LABEL = "Popularity fallback";
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
  /** Titles the exclusion filter removed from this response. */
  excluded_count: number | null;
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
    excluded_count:
      typeof candidate.excluded_count === "number" ? candidate.excluded_count : null,
  };
}

function namesLearnedRanker(policy: string): boolean {
  return policy.toLowerCase().includes(LEARNED_RANKER_STAGE);
}

/**
 * Said when a fallback response comes back for a persona that already has more
 * signals than the router asks for. Reachable since PR #64: an
 * `unseeded-retrieval` response reports `learned: false` with a high count, and
 * the counting sentence below then read "28 of the 5 watched signals".
 */
export const WARM_FALLBACK_NOTE =
  "This persona has enough watched signals; this response still came back on " +
  "the popularity fallback. The reason recorded for it is in its prediction audit.";

/** Whether the response reported a persona that is past the routing threshold. */
function pastThreshold(envelope: ServingPolicyEnvelope | null): boolean {
  const signals = envelope?.positive_signal_count;
  const threshold = envelope?.threshold;
  return (
    typeof signals === "number" && typeof threshold === "number" && signals >= threshold
  );
}

function fallbackNote(envelope: ServingPolicyEnvelope | null): string {
  if (!envelope) return FALLBACK_ROUTING_NOTE;
  const { positive_signal_count: signals, threshold } = envelope;
  if (signals === null || threshold === null) {
    // No counts to quote, so the routing rule is all that can honestly be said.
    // The response's own reason is surfaced as evidence rather than paraphrased.
    return FALLBACK_ROUTING_NOTE;
  }
  if (signals >= threshold) return WARM_FALLBACK_NOTE;
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
    // A persona past the threshold is not cold, and the cold-start label would
    // say it is.
    label: pastThreshold(envelope) ? POPULARITY_FALLBACK_LABEL : FALLBACK_POLICY_LABEL,
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

/**
 * The sentence `Why this?` opens with, before any of the evidence.
 *
 * The drawer's contents are right — the policy string, the model versions, the
 * request id, the counts — and the route contract asks for exactly them. What
 * it does not specify is the reading order, and for the viewer the product is
 * actually for, "why this?" was being answered with `item-item-cosine+lightgbm`.
 *
 * Two rules keep this honest, and they are why it is built here rather than in
 * JSX. Every number is read from the response, and a clause whose field is
 * missing is dropped rather than guessed at — there is no default seed count
 * and no assumed exclusion. And a fallback response never borrows learned
 * phrasing: it says what it is, and where a warm persona ended up on the
 * fallback anyway it says that too instead of counting signals it already has.
 *
 * Deliberately absent: any model version, policy name, hash, or request id.
 * Those are the evidence directly below it, one heading away.
 */
export function plainServingExplanation(response: RecommendationResponse): string {
  const envelope = readServingPolicyEnvelope(response);
  const learned = envelope ? envelope.learned : namesLearnedRanker(response.policy);
  const clauses = [learned ? learnedSentence(envelope) : fallbackSentence(envelope)];
  const excluded = exclusionSentence(envelope);
  if (excluded) clauses.push(excluded);
  return clauses.join(" ");
}

/**
 * The seed count the response reports, when it reports one. Read out of the
 * recorded reason — `learned-two-stage: … retrieval over 28 positive seeds, …`
 * — because that is the only place the API states the seeds retrieval actually
 * used, as distinct from the signals the persona holds.
 */
export function reportedSeedCount(
  envelope: ServingPolicyEnvelope | null,
): number | null {
  const match = /(\d+)\s+positive seeds/.exec(envelope?.reason ?? "");
  if (!match) return null;
  const seeds = Number(match[1]);
  return Number.isSafeInteger(seeds) ? seeds : null;
}

function learnedSentence(envelope: ServingPolicyEnvelope | null): string {
  const seeds = reportedSeedCount(envelope);
  // The watch-history region on the same page is the honest referent for the
  // count: it is the list those seeds came from. Individual seed titles are
  // not named, because the audit records a source-to-count map and nothing
  // that would let us name one.
  return seeds === null
    ? "Picked from the movies this persona has watched, then ordered by the ranking model."
    : `Picked from ${seeds} of the movies this persona has watched — the list further down this page — then ordered by the ranking model.`;
}

function fallbackSentence(envelope: ServingPolicyEnvelope | null): string {
  const opening = "These are the titles watched most across this tenant, not a personalised ranking.";
  if (!envelope) return opening;
  const signals = envelope.positive_signal_count;
  const threshold = envelope.threshold;
  if (signals === null || threshold === null) return opening;
  return signals >= threshold
    ? `${opening} ${WARM_FALLBACK_NOTE}`
    : `${opening} This persona has ${signals} of the ${threshold} watched movies the ranking model needs before it is used.`;
}

function exclusionSentence(envelope: ServingPolicyEnvelope | null): string | null {
  const excluded = envelope?.excluded_count;
  if (typeof excluded !== "number" || excluded <= 0) return null;
  return excluded === 1
    ? "1 title already seen or dismissed was left out."
    : `${excluded} titles already seen or dismissed were left out.`;
}
