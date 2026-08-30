import { describe, expect, it } from "vitest";

import {
  FALLBACK_POLICY_LABEL,
  FALLBACK_ROUTING_NOTE,
  LEARNED_POLICY_LABEL,
  LEARNED_SERVING_SIGNAL_THRESHOLD,
  POPULARITY_FALLBACK_LABEL,
  WARM_FALLBACK_NOTE,
  describeServingPolicy,
  plainServingExplanation,
  readServingPolicyEnvelope,
  reportedSeedCount,
  servingPolicyCopy,
} from "@/lib/discover/policy";
import type { RecommendationResponse } from "@/lib/api";
import {
  fallbackRecommendations,
  learnedRecommendations,
} from "@/lib/fixtures/discover-fixtures";

describe("serving policy classification", () => {
  it("calls the popularity fallback what it is", () => {
    const policy = servingPolicyCopy("popularity");

    expect(policy.kind).toBe("fallback");
    expect(policy.label).toBe(FALLBACK_POLICY_LABEL);
    expect(policy.raw).toBe("popularity");
  });

  it("reports learned serving only when the response names the ranker", () => {
    expect(servingPolicyCopy("item-item+lightgbm").kind).toBe("learned");
    expect(servingPolicyCopy("two-tower+lightgbm").kind).toBe("learned");
    expect(servingPolicyCopy("genre-affinity").kind).toBe("fallback");
    expect(servingPolicyCopy("").kind).toBe("fallback");
  });

  it("keeps the reported string intact instead of a prettier substitute", () => {
    expect(servingPolicyCopy("  item-item+lightgbm  ").raw).toBe("item-item+lightgbm");
    expect(servingPolicyCopy("item-item+lightgbm").label).toBe(LEARNED_POLICY_LABEL);
  });

  it("never claims learning in fallback copy", () => {
    const fallback = servingPolicyCopy("popularity");

    expect(fallback.summary).toContain("did not report learned serving");
    expect(fallback.summary.toLowerCase()).not.toContain("because you");
    expect(fallback.summary.toLowerCase()).not.toContain("learned from your");
  });

  it("states the routing threshold as policy, not as this response's reason", () => {
    expect(FALLBACK_ROUTING_NOTE).toContain(String(LEARNED_SERVING_SIGNAL_THRESHOLD));
    expect(FALLBACK_ROUTING_NOTE).toContain("prediction audit");
  });

  it("classifies the recorded contract responses the way the API reported them", () => {
    expect(describeServingPolicy(learnedRecommendations).kind).toBe("learned");
    expect(describeServingPolicy(fallbackRecommendations).kind).toBe("fallback");
  });

  it("still classifies a response that arrives without the envelope", () => {
    expect(describeServingPolicy(withoutEnvelope(learnedRecommendations)).kind).toBe(
      "learned",
    );
    expect(describeServingPolicy(withoutEnvelope(fallbackRecommendations)).kind).toBe(
      "fallback",
    );
    expect(describeServingPolicy(withoutEnvelope(learnedRecommendations)).reported).toBe(
      false,
    );
  });
});

/**
 * Overrides the response's `serving_policy` so the preference order can be
 * exercised, including the degraded shapes a proxy or an older deployment can
 * still produce. The cast is the point of these cases: they cover envelopes
 * the generated type says cannot exist.
 */
function withEnvelope(
  response: RecommendationResponse,
  envelope: Record<string, unknown>,
): RecommendationResponse {
  return { ...response, serving_policy: envelope } as unknown as RecommendationResponse;
}

/** Removes the envelope entirely, leaving only the flat `policy` string. */
function withoutEnvelope(response: RecommendationResponse): RecommendationResponse {
  const rest: Record<string, unknown> = { ...response };
  delete rest.serving_policy;
  return rest as unknown as RecommendationResponse;
}

describe("the reported serving policy wins over the inferred one", () => {
  it("believes an explicit learned flag rather than the policy string", () => {
    const policy = describeServingPolicy(
      withEnvelope(fallbackRecommendations, {
        name: "item-item+lightgbm",
        learned: true,
        positive_signal_count: 12,
        threshold: 10,
        reason: null,
      }),
    );

    expect(policy.kind).toBe("learned");
    expect(policy.raw).toBe("item-item+lightgbm");
    expect(policy.reported).toBe(true);
  });

  it("believes an explicit fallback flag even when the name mentions the ranker", () => {
    const policy = describeServingPolicy(
      withEnvelope(learnedRecommendations, {
        name: "lightgbm-unavailable",
        learned: false,
        positive_signal_count: 2,
        threshold: 10,
        reason: "model-server-unavailable",
      }),
    );

    expect(policy.kind).toBe("fallback");
    expect(policy.note).toContain("2 of the 10 watched signals");
    // The recorded reason is quoted as evidence, never paraphrased into copy.
    expect(policy.note).not.toContain("model-server-unavailable");
    expect(policy.reason).toBe("model-server-unavailable");
  });

  it("carries the reported score scale so the caveat can name it", () => {
    expect(describeServingPolicy(learnedRecommendations).scoreScale).toBe(
      "lightgbm-rank-score",
    );
    expect(describeServingPolicy(fallbackRecommendations).scoreScale).toBe(
      "tenant-interaction-count",
    );
  });

  it("falls back to the routing rule when the response reports no counts", () => {
    const policy = describeServingPolicy(
      withEnvelope(fallbackRecommendations, { name: "popularity", learned: false }),
    );

    expect(policy.note).toContain(String(LEARNED_SERVING_SIGNAL_THRESHOLD));
    expect(policy.reported).toBe(true);
  });

  it("ignores a malformed envelope instead of trusting half of it", () => {
    expect(
      readServingPolicyEnvelope(withEnvelope(learnedRecommendations, { name: "x" })),
    ).toBeNull();
    expect(readServingPolicyEnvelope(withoutEnvelope(learnedRecommendations))).toBeNull();
    expect(
      describeServingPolicy(withEnvelope(learnedRecommendations, { learned: "yes" })).kind,
    ).toBe("learned");
  });
});

describe("the plain sentence Why this? opens with", () => {
  function withPolicy(
    base: RecommendationResponse,
    overrides: Partial<RecommendationResponse["serving_policy"]>,
  ): RecommendationResponse {
    return { ...base, serving_policy: { ...base.serving_policy, ...overrides } };
  }

  it("says where a learned response came from, in the viewer's words", () => {
    const sentence = plainServingExplanation(learnedRecommendations);

    expect(sentence).toContain("Picked from 12 of the movies this persona has watched");
    expect(sentence).toContain("ordered by the ranking model");
    // No version, no policy name, no hash, no request id: that is the evidence
    // directly beneath it, one heading away.
    expect(sentence).not.toMatch(/lightgbm|item-item|v\d|sha256|[0-9a-f]{8}-/i);
  });

  it("counts what was left out, and does not say so when nothing was", () => {
    expect(plainServingExplanation(learnedRecommendations)).toContain(
      "9 titles already seen or dismissed were left out.",
    );
    expect(
      plainServingExplanation(withPolicy(learnedRecommendations, { excluded_count: 0 })),
    ).not.toContain("left out");
    expect(
      plainServingExplanation(withPolicy(learnedRecommendations, { excluded_count: 1 })),
    ).toContain("1 title already seen or dismissed was left out.");
  });

  it("drops the seed clause rather than guessing when the reason names no count", () => {
    const sentence = plainServingExplanation(
      withPolicy(learnedRecommendations, { reason: "learned-two-stage" }),
    );

    expect(sentence).toContain("Picked from the movies this persona has watched");
    expect(sentence).not.toMatch(/Picked from \d/);
  });

  it("never lends learned phrasing to a fallback response", () => {
    const sentence = plainServingExplanation(fallbackRecommendations);

    expect(sentence).toContain("watched most across this tenant");
    expect(sentence).toContain("3 of the 10 watched movies");
    expect(sentence).not.toContain("Picked from");
  });

  it("tells a warm persona on the fallback what actually happened", () => {
    // Reachable since PR #64: `unseeded-retrieval` reports `learned: false`
    // with a signal count far past the threshold.
    const sentence = plainServingExplanation(
      withPolicy(fallbackRecommendations, {
        positive_signal_count: 28,
        learned: false,
        name: "popularity-fill+lightgbm",
      }),
    );

    expect(sentence).toContain("enough watched signals");
    expect(sentence).not.toContain("28 of the 10");
    expect(sentence).not.toMatch(/\d+ of the \d+ watched movies/);
  });

  it("falls back to the shape of the answer when the envelope is missing", () => {
    const withoutEnvelope = { ...fallbackRecommendations };
    delete (withoutEnvelope as { serving_policy?: unknown }).serving_policy;

    const sentence = plainServingExplanation(withoutEnvelope);
    expect(sentence).toBe(
      "These are the titles watched most across this tenant, not a personalised ranking.",
    );
  });

  it("reads the seed count out of the recorded reason, or nothing at all", () => {
    expect(reportedSeedCount(readServingPolicyEnvelope(learnedRecommendations))).toBe(12);
    expect(reportedSeedCount(readServingPolicyEnvelope(fallbackRecommendations))).toBeNull();
    expect(reportedSeedCount(null)).toBeNull();
  });
});

describe("a fallback served to a persona that is not cold", () => {
  it("drops the cold-start label and says what happened instead", () => {
    const warm = describeServingPolicy({
      ...fallbackRecommendations,
      serving_policy: {
        ...fallbackRecommendations.serving_policy,
        positive_signal_count: 28,
      },
    });

    expect(warm.label).toBe(POPULARITY_FALLBACK_LABEL);
    expect(warm.label).not.toBe(FALLBACK_POLICY_LABEL);
    expect(warm.note).toBe(WARM_FALLBACK_NOTE);
    expect(warm.note).not.toContain("28 of the 10");
  });

  it("keeps the cold-start label and the count while the persona is cold", () => {
    const cold = describeServingPolicy(fallbackRecommendations);

    expect(cold.label).toBe(FALLBACK_POLICY_LABEL);
    expect(cold.note).toContain("3 of the 10 watched signals");
  });
});
