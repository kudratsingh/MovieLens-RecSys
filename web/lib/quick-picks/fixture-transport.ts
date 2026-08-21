/**
 * A recorded stand-in for the durable boundary.
 *
 * The preview route and the component tests need something that behaves like
 * the API — canonical state back, revisions that move, a rating that implies
 * watched, a dismissal that clears the watchlist — without a database. The
 * transitions below mirror `src/serving/feedback.py`; keeping them faithful is
 * the point, because a simulation that is kinder than the real thing would hide
 * exactly the bugs these tests exist to catch.
 *
 * Test and preview input only. The live route builds its transport from
 * `createLiveQuickPickTransport` and never imports this module.
 */

import type { MovieState, RecommendationResponse } from "@/lib/api";
import type { QuickPickCommitRequest } from "@/lib/quick-picks/contract";
import type {
  QuickPickCommitOutcome,
  QuickPickQueuePayload,
  QuickPickTransport,
} from "@/lib/quick-picks/transport";
import { hasResourceData, readyState } from "@/lib/resources/state";

const FIXTURE_NOW = "2026-08-21T12:00:00.000Z";

export const FIXTURE_COMMIT_FAILURE =
  "The recommendation API could not save that decision.";

function baseState(movieId: number, response: RecommendationResponse): MovieState {
  return {
    dismissed_at: null,
    movie_id: movieId,
    rating: null,
    rating_updated_at: null,
    revision: 0,
    tenant_id: response.tenant_id,
    updated_at: FIXTURE_NOW,
    user_id: response.user_id,
    watched_at: null,
    watchlisted_at: null,
  };
}

function transition(
  previous: MovieState,
  request: QuickPickCommitRequest,
): MovieState {
  const next = { ...previous, revision: previous.revision + 1, updated_at: FIXTURE_NOW };
  switch (request.action) {
    case "watchlist":
      return { ...next, watchlisted_at: previous.watchlisted_at ?? FIXTURE_NOW };
    case "watched":
      return {
        ...next,
        watched_at: previous.watched_at ?? FIXTURE_NOW,
        watchlisted_at: null,
        rating: request.rating ?? previous.rating,
        rating_updated_at: request.rating === null ? previous.rating_updated_at : FIXTURE_NOW,
      };
    case "dismiss":
      return {
        ...next,
        dismissed_at: previous.dismissed_at ?? FIXTURE_NOW,
        watchlisted_at: null,
      };
    case "undo-dismiss":
      return { ...next, dismissed_at: null };
  }
}

export function createFixtureQuickPickTransport(options: {
  initial: QuickPickQueuePayload;
  /** Fails every commit, for the rollback and failure-evidence states. */
  failCommits?: boolean;
  resolveSeedTitle?: (movieId: number) => string | null;
}): QuickPickTransport {
  const committed = new Map<number, MovieState>();
  const response = hasResourceData(options.initial.queue)
    ? options.initial.queue.data
    : null;

  return {
    async commit(request): Promise<QuickPickCommitOutcome> {
      if (options.failCommits || !response) {
        return { ok: false, message: FIXTURE_COMMIT_FAILURE };
      }
      const previous =
        committed.get(request.movieId) ?? baseState(request.movieId, response);
      const next = transition(previous, request);
      committed.set(request.movieId, next);
      return { ok: true, state: next };
    },

    async refresh(): Promise<QuickPickQueuePayload> {
      if (!response) return options.initial;
      const excluded = new Set(
        [...committed.entries()]
          .filter(([, state]) => state.watched_at !== null || state.dismissed_at !== null)
          .map(([movieId]) => movieId),
      );
      const watchedCount = [...committed.values()].filter(
        (state) => state.watched_at !== null,
      ).length;
      const refreshed: RecommendationResponse = {
        ...response,
        items: response.items.filter((item) => !excluded.has(item.movie_id)),
        serving_policy: {
          ...response.serving_policy,
          excluded_count: response.serving_policy.excluded_count + excluded.size,
          positive_signal_count:
            response.serving_policy.positive_signal_count + watchedCount,
        },
      };
      return {
        queue: readyState(
          "recommendations",
          refreshed,
          hasResourceData(options.initial.queue)
            ? options.initial.queue.requestId
            : "fixture-refresh",
          "recorded-contract-fixture",
        ),
        evidence: options.initial.evidence,
      };
    },

    async resolveSeedTitle(movieId) {
      return options.resolveSeedTitle?.(movieId) ?? null;
    },
  };
}
