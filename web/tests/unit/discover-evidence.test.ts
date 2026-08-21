import { describe, expect, it } from "vitest";

import {
  featureRows,
  formatRankScore,
  rankScoreCaveat,
  recommendationEvidence,
} from "@/lib/discover/evidence";
import {
  fallbackRecommendations,
  learnedRecommendations,
} from "@/lib/fixtures/discover-fixtures";

const REQUEST_ID = "2a7a63d1-4a5f-4d20-9a58-2ce0d4e9a111";

describe("Why this? evidence", () => {
  it("repeats only fields the response actually carried", () => {
    const evidence = recommendationEvidence(
      learnedRecommendations,
      learnedRecommendations.items[0],
      REQUEST_ID,
    );

    expect(evidence.rows.map((row) => row.term)).toEqual([
      "Serving policy",
      "Recorded reason",
      "Model version",
      "Tenant",
      "Request",
    ]);
    expect(evidence.rows.find((row) => row.term === "Request")?.detail).toBe(REQUEST_ID);
  });

  it("drops a row rather than rendering a confident blank", () => {
    const evidence = recommendationEvidence(
      { ...learnedRecommendations, model_version: "   " },
      learnedRecommendations.items[0],
      null,
    );

    expect(evidence.rows.map((row) => row.term)).toEqual([
      "Serving policy",
      "Recorded reason",
      "Tenant",
    ]);
  });

  it("uses the API's own reason verbatim and never invents a liked-title claim", () => {
    for (const response of [learnedRecommendations, fallbackRecommendations]) {
      const evidence = recommendationEvidence(response, response.items[0], REQUEST_ID);
      const rendered = [evidence.reason ?? "", ...evidence.rows.map((row) => row.detail)]
        .join(" ")
        .toLowerCase();

      expect(evidence.reason).toBe(response.items[0].reason);
      expect(rendered).not.toContain("because you liked");
      expect(rendered).not.toContain("% match");
      expect(rendered).not.toContain("match score");
    }
  });

  it("reports a missing item reason as missing", () => {
    const evidence = recommendationEvidence(
      learnedRecommendations,
      { ...learnedRecommendations.items[0], reason: "  " },
      REQUEST_ID,
    );

    expect(evidence.reason).toBeNull();
  });

  it("formats the rank score as an ordering value, never as a percentage", () => {
    expect(formatRankScore(4.82134)).toBe("4.8213");
    expect(formatRankScore(Number.NaN)).toBe("unavailable");
    expect(rankScoreCaveat()).toContain("not a probability or a match percentage");
    expect(rankScoreCaveat("lightgbm-rank-score")).toContain("lightgbm-rank-score");
  });

  it("renders audit feature values in a stable order", () => {
    expect(featureRows({ b_feature: 1.5, a_feature: 3 })).toEqual([
      { term: "a_feature", detail: "3" },
      { term: "b_feature", detail: "1.5000" },
    ]);
  });
});
