/**
 * Structured "why this?" evidence for a Quick Picks card.
 *
 * The only honest sources of a per-item explanation are the prediction audit's
 * `candidate_source` and `seed_movie_id`. Two rules follow from ADR 0012 and
 * are enforced here rather than in copy review:
 *
 * 1. The audit has to be the one that produced *this* queue. Rows are matched
 *    on the correlation ID the BFF sent with the recommendations request, so a
 *    concurrent request's explanation can never be attached to these cards.
 * 2. The LambdaRank score is never part of the output. It is an uncalibrated
 *    ordering value, which is exactly what a match percentage must not be.
 */

import type { RecommendationAuditResponse } from "@/lib/api";

export type QuickPickEvidence = {
  candidateSource: string;
  /** A positive history title the item-item index was walked from. */
  seedMovieId: number | null;
};

export type QuickPickEvidenceMap = Readonly<Record<number, QuickPickEvidence>>;

export const EMPTY_EVIDENCE: QuickPickEvidenceMap = {};

export function quickPickEvidence(
  audits: RecommendationAuditResponse | null,
  correlationId: string | null,
): QuickPickEvidenceMap {
  if (!audits || !correlationId) return EMPTY_EVIDENCE;
  const audit = audits.items.find((item) => item.correlation_id === correlationId);
  if (!audit) return EMPTY_EVIDENCE;

  const evidence: Record<number, QuickPickEvidence> = {};
  for (const prediction of audit.predictions) {
    evidence[prediction.movie_id] = {
      candidateSource: prediction.candidate_source,
      seedMovieId: prediction.seed_movie_id ?? null,
    };
  }
  return evidence;
}

const SOURCE_COPY: Record<string, string> = {
  "item-item-cosine": "Retrieved by item-item similarity, then ranked by LightGBM.",
  "popularity-fill":
    "Added by popularity fill because similarity retrieval returned too few titles.",
  "popularity-fallback": "Selected by tenant popularity while the model learns.",
};

/**
 * The seed title is only ever named when the audit actually recorded a seed and
 * the title was resolved. Everything else degrades to the source, which is the
 * weakest claim we can still prove.
 */
export function evidenceSentence(
  evidence: QuickPickEvidence | undefined,
  seedTitle: string | null,
): string | null {
  if (!evidence) return null;
  if (evidence.seedMovieId !== null) {
    return seedTitle
      ? `Retrieved as similar to ${seedTitle}, which this persona has watched.`
      : "Retrieved as similar to a movie in this persona's watched history.";
  }
  return (
    SOURCE_COPY[evidence.candidateSource] ??
    `Candidate source: ${evidence.candidateSource}.`
  );
}
