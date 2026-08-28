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
  fallbackRecommendationResponse,
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
    ["fallback recommendations", isRecommendationResponse, fallbackRecommendationResponse],
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

  it("accepts a prediction with no seed, as popularity fill produces", () => {
    expect(
      isRecommendationAuditResponse({
        ...auditResponse,
        items: [
          {
            ...auditResponse.items[0],
            predictions: [
              {
                candidate_source: "popularity-fill",
                features: { user_interaction_count: 12 },
                movie_id: 1,
                score: 0.1,
                seed_movie_id: null,
              },
            ],
          },
        ],
      }),
    ).toBe(true);
  });

  it("accepts an empty collection and a null poster", () => {
    expect(isRecommendationResponse({ ...recommendationResponse, items: [] })).toBe(true);
    expect(
      isCatalogResponse({
        ...catalogResponse,
        items: [{ ...catalogResponse.items[0], poster_url: null, state: null }],
      }),
    ).toBe(true);
  });

  it("accepts a detail record carrying the enriched TMDB block", () => {
    expect(
      isMovieDetailResponse({
        ...movieDetailResponse,
        item: {
          ...movieDetailResponse.item,
          details: {
            tagline: "Two women. Two cons.",
            runtime_minutes: 145,
            release_date: "2016-06-01",
            backdrop_url: "https://image.tmdb.org/t/p/w1280/backdrop.jpg",
            tmdb_rating: { average: 8.1, count: 4812 },
            directors: ["Park Chan-wook"],
            cast: [
              { name: "Kim Min-hee", character: "Lady Hideko", profile_url: null },
            ],
            trailer: { provider: "youtube", key: "T7kfW4trvUM", name: "Trailer" },
            fetched_at: "2026-08-24T09:00:00Z",
          },
        },
      }),
    ).toBe(true);
  });

  it("accepts an enriched block whose optional fields are all absent", () => {
    expect(
      isMovieDetailResponse({
        ...movieDetailResponse,
        item: {
          ...movieDetailResponse.item,
          details: {
            tagline: null,
            runtime_minutes: null,
            release_date: null,
            backdrop_url: null,
            tmdb_rating: null,
            directors: [],
            cast: [],
            trailer: null,
            fetched_at: "2026-08-24T09:00:00Z",
          },
        },
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

  it("rejects a detail record that omits the enriched block entirely", () => {
    // `details` is required and nullable. An omitted key is drift, and the page
    // would otherwise render the degraded movie with nothing having reported a
    // problem — the quiet failure the boundary exists to make loud.
    expect(
      isMovieDetailResponse({
        ...movieDetailResponse,
        item: without(movieDetailResponse.item, "details"),
      }),
    ).toBe(false);
  });

  it("rejects an enriched block the page could not render", () => {
    const enriched = {
      tagline: null,
      runtime_minutes: 145,
      release_date: null,
      backdrop_url: null,
      tmdb_rating: null,
      directors: [],
      cast: [],
      trailer: null,
      fetched_at: "2026-08-24T09:00:00Z",
    };
    const withDetails = (details: unknown) => ({
      ...movieDetailResponse,
      item: { ...movieDetailResponse.item, details },
    });

    // A provider this UI has no embed for: the key would be interpolated into a
    // URL against a host the client has never heard of.
    expect(
      isMovieDetailResponse(
        withDetails({
          ...enriched,
          trailer: { provider: "vimeo", key: "123", name: "Trailer" },
        }),
      ),
    ).toBe(false);
    // A score with no vote count is half a claim.
    expect(
      isMovieDetailResponse(withDetails({ ...enriched, tmdb_rating: { average: 8.1 } })),
    ).toBe(false);
    // A runtime that arrived as a string would format as "NaNh NaNm".
    expect(
      isMovieDetailResponse(withDetails({ ...enriched, runtime_minutes: "145" })),
    ).toBe(false);
    expect(
      isMovieDetailResponse(withDetails({ ...enriched, cast: [{ character: "Hideko" }] })),
    ).toBe(false);
  });

  it("rejects a recommendation response that cannot state its serving policy", () => {
    // Without the policy block the route cannot tell fallback from learned, so
    // it must fail rather than render copy it cannot substantiate.
    expect(isRecommendationResponse(without(recommendationResponse, "serving_policy"))).toBe(
      false,
    );
    expect(
      isRecommendationResponse({
        ...recommendationResponse,
        serving_policy: { ...recommendationResponse.serving_policy, learned: "true" },
      }),
    ).toBe(false);
    expect(
      isRecommendationResponse({
        ...recommendationResponse,
        serving_policy: without(recommendationResponse.serving_policy, "score_scale"),
      }),
    ).toBe(false);
  });

  it("rejects an audit prediction with no candidate attribution", () => {
    expect(
      isRecommendationAuditResponse({
        ...auditResponse,
        items: [
          {
            ...auditResponse.items[0],
            predictions: [{ features: { a: 1 }, movie_id: 1, score: 0.1 }],
          },
        ],
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
