import { describe, expect, it } from "vitest";

import type { RecommendationAuditResponse } from "@/lib/api";
import { evidenceSentence, quickPickEvidence } from "@/lib/quick-picks/evidence";

import { auditResponse } from "./resource-fixtures";

const correlationId = auditResponse.items[0].correlation_id;

function withItems(
  items: RecommendationAuditResponse["items"],
): RecommendationAuditResponse {
  return { ...auditResponse, items };
}

describe("matching evidence to the queue it explains", () => {
  it("uses the correlation ID the recommendations response carried", () => {
    const evidence = quickPickEvidence(auditResponse, correlationId);

    const prediction = auditResponse.items[0].predictions[0];
    expect(evidence[prediction.movie_id]).toEqual({
      candidateSource: prediction.candidate_source,
      seedMovieId: prediction.seed_movie_id,
    });
    expect(prediction.seed_movie_id).toBeGreaterThan(0);
  });

  it("returns nothing when the audit belongs to a different request", () => {
    expect(quickPickEvidence(auditResponse, "some-other-correlation-id")).toEqual({});
    expect(quickPickEvidence(auditResponse, null)).toEqual({});
    expect(quickPickEvidence(null, correlationId)).toEqual({});
  });

  it("carries no score into the view model at all", () => {
    const evidence = quickPickEvidence(auditResponse, correlationId);

    expect(JSON.stringify(evidence)).not.toContain("score");
    expect(JSON.stringify(evidence)).not.toContain("0.82");
  });
});

describe("evidence copy", () => {
  it("names the seed title when the audit recorded one and it resolved", () => {
    expect(
      evidenceSentence({ candidateSource: "item-item-cosine", seedMovieId: 103 }, "Memories of Murder"),
    ).toBe("Retrieved as similar to Memories of Murder, which this persona has watched.");
  });

  it("degrades to the weakest true statement when the title did not resolve", () => {
    expect(
      evidenceSentence({ candidateSource: "item-item-cosine", seedMovieId: 103 }, null),
    ).toBe("Retrieved as similar to a movie in this persona's watched history.");
  });

  it("falls back to the source when there is no seed", () => {
    expect(
      evidenceSentence({ candidateSource: "popularity-fallback", seedMovieId: null }, null),
    ).toBe("Selected by tenant popularity while the model learns.");
    expect(
      evidenceSentence({ candidateSource: "something-new", seedMovieId: null }, null),
    ).toBe("Candidate source: something-new.");
  });

  it("says nothing at all rather than guessing when no audit matched", () => {
    expect(evidenceSentence(undefined, "Memories of Murder")).toBeNull();
  });

  it("ignores a prediction's seed when the audit did not record one", () => {
    const audit = withItems([
      {
        ...auditResponse.items[0],
        predictions: [
          {
            candidate_source: "popularity-fallback",
            features: {},
            movie_id: 105,
            score: 12,
          },
        ],
      },
    ]);

    expect(quickPickEvidence(audit, correlationId)[105]).toEqual({
      candidateSource: "popularity-fallback",
      seedMovieId: null,
    });
  });
});
