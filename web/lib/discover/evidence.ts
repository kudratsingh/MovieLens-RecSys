/**
 * What `Why this?` is allowed to say.
 *
 * The rule from ADR 0012 §7 is narrow and worth restating: an explanation may
 * only repeat structured evidence the response actually carries. Today that is
 * the serving policy and its recorded reason, the model version, the tenant,
 * the correlation ID, and the API's own item-level `reason` string. It is
 * deliberately *not*:
 *
 * - `Because you liked …`, because star magnitude is not a learned input and
 *   nothing in the response names a contributing title; or
 * - a match percentage, because the ranker score is an uncalibrated ordering
 *   value and presenting it as a probability would be an invented claim.
 *
 * Building the rows here — rather than in JSX — means a missing field drops a
 * row instead of rendering a confident-looking blank, and it gives the rule a
 * place to be tested.
 */

import type { RecommendationItem, RecommendationResponse } from "@/lib/api";
import { describeServingPolicy, type ServingPolicyCopy } from "@/lib/discover/policy";

export type EvidenceRow = { term: string; detail: string };

export type RecommendationEvidence = {
  policy: ServingPolicyCopy;
  /** The API's own item reason, verbatim, or null when it sent none. */
  reason: string | null;
  rows: readonly EvidenceRow[];
};

const RANK_SCORE_CAVEAT_BASE =
  "Uncalibrated ranking value used for ordering only. It is not a probability or a match percentage.";

/**
 * Said wherever the raw ranking score is shown, so it cannot read as a score
 * out of 100. When the response names its scale, the caveat names it too —
 * a popularity fallback and a LambdaRank ordering are different numbers and
 * saying so is cheaper than letting a reader assume they are comparable.
 */
export function rankScoreCaveat(scoreScale: string | null = null): string {
  return scoreScale
    ? `${RANK_SCORE_CAVEAT_BASE} The response reported the scale as \`${scoreScale}\`.`
    : RANK_SCORE_CAVEAT_BASE;
}

function row(term: string, detail: string | null | undefined): EvidenceRow | null {
  const value = typeof detail === "string" ? detail.trim() : "";
  return value ? { term, detail: value } : null;
}

export function recommendationEvidence(
  response: RecommendationResponse,
  item: RecommendationItem,
  requestId: string | null,
): RecommendationEvidence {
  const policy = describeServingPolicy(response);
  const rows = [
    row("Serving policy", policy.raw),
    // The response's own words about why this policy ran, quoted rather than
    // paraphrased into something the API never said.
    row("Recorded reason", policy.reason),
    row("Model version", response.model_version),
    row("Tenant", response.tenant_id),
    row("Request", requestId),
  ].filter((entry): entry is EvidenceRow => entry !== null);

  const reason = item.reason.trim();
  return { policy, reason: reason.length > 0 ? reason : null, rows };
}

/**
 * Four decimals keeps the ordering legible between adjacent candidates without
 * implying more precision than an ordering value has.
 */
export function formatRankScore(score: number): string {
  return Number.isFinite(score) ? score.toFixed(4) : "unavailable";
}

/** Feature values arrive as an open name → number map; render them as given. */
export function featureRows(features: Record<string, number>): readonly EvidenceRow[] {
  return Object.entries(features)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => ({
      term: name,
      detail: Number.isInteger(value) ? String(value) : value.toFixed(4),
    }));
}
