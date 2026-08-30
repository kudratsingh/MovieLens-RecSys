/**
 * Recorded Quick Picks queues for component tests, the screenshot harness, and
 * the isolated preview route.
 *
 * These are contract-shaped `RecommendationResponse` payloads rather than view
 * models on purpose: the preview then exercises the same mapping, validators,
 * and policy branching as the live route, which is what makes a fixture-mode
 * screenshot evidence of anything. Reaching them still goes through the 5A
 * fixture gate, so a production read cannot land here.
 */

import type { RecommendationItem, RecommendationResponse, ServingPolicy } from "@/lib/api";
import type { QuickPickEvidenceMap } from "@/lib/quick-picks/evidence";
import { movies } from "@/lib/fixtures/movie-fixtures";

export const FIXTURE_QUEUE_MOVIE_IDS = [105, 102, 107, 109, 101, 106, 110] as const;

const FIXTURE_SCORES: Record<number, number> = {
  105: 0.91,
  102: 0.84,
  107: 0.77,
  109: 0.71,
  101: 0.66,
  106: 0.6,
  110: 0.55,
};

function fixtureItem(movieId: number, learned: boolean): RecommendationItem {
  const movie = movies.find((candidate) => candidate.id === movieId);
  if (!movie) throw new Error(`No recorded movie fixture for ${movieId}`);
  return {
    genres: [...movie.genres],
    metadata_source: movie.posterSrc ? "reviewed-fixture" : "movielens",
    movie_id: movie.id,
    overview: movie.overview,
    poster_url: movie.posterSrc,
    reason: learned
      ? "Similar to movies in this persona's watched history"
      : "Popular with viewers in this tenant",
    release_year: movie.year,
    score: FIXTURE_SCORES[movie.id] ?? 0.5,
    title: movie.title,
    tmdb_id: null,
    // The queue only ever holds titles serving has not excluded, so the
    // recorded cards carry no prior movie state.
    state: null,
  };
}

export const fixtureFallbackPolicy: ServingPolicy = {
  excluded_count: 3,
  filter_policy: "watched-and-dismissed-excluded-v1",
  learned: false,
  name: "popularity",
  positive_signal_count: 2,
  reason: "cold-start: 2 positive watched signals below threshold 10",
  score_scale: "tenant-interaction-count",
  threshold: 10,
};

export const fixtureLearnedPolicy: ServingPolicy = {
  excluded_count: 11,
  filter_policy: "watched-and-dismissed-excluded-v1",
  learned: true,
  name: "item-item-cosine+lightgbm",
  positive_signal_count: 12,
  reason:
    "learned-two-stage: item-item-cosine retrieval over 12 positive seeds, ranked by lgbm-ranker-2026.08",
  score_scale: "lightgbm-rank-score",
  threshold: 10,
};

export function fixtureQuickPickResponse(
  options: { learned?: boolean; movieIds?: readonly number[] } = {},
): RecommendationResponse {
  const learned = options.learned ?? false;
  const policy = learned ? fixtureLearnedPolicy : fixtureFallbackPolicy;
  return {
    items: (options.movieIds ?? FIXTURE_QUEUE_MOVIE_IDS).map((movieId) =>
      fixtureItem(movieId, learned),
    ),
    model_version: learned ? "item-item-v3/lgbm-ranker-2026.08" : "popularity-v1",
    policy: policy.name,
    serving_policy: policy,
    tenant_id: "demo",
    user_id: 900000101,
  };
}

/** The seed is a watched title, mirroring what an item-item audit records. */
export const FIXTURE_SEED_MOVIE_ID = 103;

export function fixtureQuickPickEvidence(learned: boolean): QuickPickEvidenceMap {
  const evidence: Record<number, { candidateSource: string; seedMovieId: number | null }> = {};
  for (const movieId of FIXTURE_QUEUE_MOVIE_IDS) {
    evidence[movieId] = learned
      ? { candidateSource: "item-item-cosine", seedMovieId: FIXTURE_SEED_MOVIE_ID }
      : { candidateSource: "popularity-fallback", seedMovieId: null };
  }
  return evidence;
}

export function fixtureMovieTitle(movieId: number): string | null {
  return movies.find((movie) => movie.id === movieId)?.title ?? null;
}
