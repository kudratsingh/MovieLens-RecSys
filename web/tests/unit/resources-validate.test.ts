import { describe, expect, it } from "vitest";

import {
  isCatalogResponse,
  isFeedbackMutationResponse,
  isHistoryResponse,
  isLibraryResponse,
  isMovieDetailResponse,
  isMovieState,
  isOnlineUserFeatures,
  isRecommendationAuditResponse,
  isRecommendationResponse,
  isTasteSummaryResponse,
} from "@/lib/resources/validate";

import {
  auditResponse,
  catalogResponse,
  libraryResponse,
  movieDetailResponse,
  movieState,
  onlineFeatures,
  recommendationResponse,
  tasteSummaryResponse,
} from "./resource-fixtures";

function without<T extends object>(value: T, key: keyof T & string) {
  const copy = { ...value } as Record<string, unknown>;
  delete copy[key];
  return copy;
}

describe("runtime validators accept the published contract", () => {
  const contracts: ReadonlyArray<[string, (value: unknown) => boolean, unknown]> = [
    ["recommendations", isRecommendationResponse, recommendationResponse],
    ["catalog", isCatalogResponse, catalogResponse],
    ["movie detail", isMovieDetailResponse, movieDetailResponse],
    ["library", isLibraryResponse, libraryResponse],
    ["taste summary", isTasteSummaryResponse, tasteSummaryResponse],
    ["audits", isRecommendationAuditResponse, auditResponse],
    ["online features", isOnlineUserFeatures, onlineFeatures],
    ["movie state", isMovieState, movieState],
  ];

  for (const [name, guard, payload] of contracts) {
    it(`accepts a ${name} payload`, () => {
      expect(guard(payload)).toBe(true);
    });
  }

  it("accepts an empty collection and a null poster", () => {
    expect(isRecommendationResponse({ ...recommendationResponse, items: [] })).toBe(true);
    expect(
      isCatalogResponse({
        ...catalogResponse,
        items: [{ ...catalogResponse.items[0], poster_url: null, state: null }],
      }),
    ).toBe(true);
  });

  it("accepts a history payload", () => {
    expect(
      isHistoryResponse({
        items: [
          { genres: ["Drama"], movie_id: 1, rating: null, timestamp: 1_700_000_000, title: "A" },
        ],
        tenant_id: "demo",
        user_id: 900000101,
      }),
    ).toBe(true);
  });
});

describe("runtime validators reject payloads the UI cannot render", () => {
  it("rejects non-objects and proxy error pages", () => {
    for (const payload of [null, undefined, "<html>502</html>", 7, []]) {
      expect(isRecommendationResponse(payload)).toBe(false);
      expect(isCatalogResponse(payload)).toBe(false);
      expect(isLibraryResponse(payload)).toBe(false);
    }
  });

  it("rejects a response that lost its tenant scope", () => {
    expect(isRecommendationResponse(without(recommendationResponse, "tenant_id"))).toBe(false);
    expect(isLibraryResponse({ ...libraryResponse, user_id: "900000101" })).toBe(false);
  });

  it("rejects an item whose required fields drifted", () => {
    expect(
      isRecommendationResponse({
        ...recommendationResponse,
        items: [{ ...recommendationResponse.items[0], score: "0.82" }],
      }),
    ).toBe(false);
    expect(
      isCatalogResponse({
        ...catalogResponse,
        items: [{ ...catalogResponse.items[0], source_status: "degraded" }],
      }),
    ).toBe(false);
  });

  it("rejects a taste summary that drops its non-model attribution", () => {
    expect(isTasteSummaryResponse({ ...tasteSummaryResponse, source: "ranker-v2" })).toBe(false);
  });

  it("rejects a library page with an unknown tab or sort", () => {
    expect(isLibraryResponse({ ...libraryResponse, tab: "dismissed" })).toBe(false);
    expect(isLibraryResponse({ ...libraryResponse, sort: "score" })).toBe(false);
  });

  it("rejects movie state without a revision to reconcile against", () => {
    expect(isMovieState(without(movieState, "revision"))).toBe(false);
    expect(isMovieState({ ...movieState, rating: "4.5" })).toBe(false);
  });

  it("rejects an audit whose prediction features are not numeric", () => {
    expect(
      isRecommendationAuditResponse({
        ...auditResponse,
        items: [
          {
            ...auditResponse.items[0],
            predictions: [{ features: { a: "1" }, movie_id: 1, score: 0.1 }],
          },
        ],
      }),
    ).toBe(false);
  });

  it("rejects a mutation response missing its committed state", () => {
    expect(
      isFeedbackMutationResponse({
        outcome: "changed",
        replayed: false,
        request_id: "abc",
      }),
    ).toBe(false);
    expect(
      isFeedbackMutationResponse({
        outcome: "changed",
        replayed: false,
        request_id: "abc",
        state: movieState,
      }),
    ).toBe(true);
  });

  it("rejects online features that lost their freshness stamp", () => {
    expect(isOnlineUserFeatures(without(onlineFeatures, "feature_timestamp"))).toBe(false);
  });
});
