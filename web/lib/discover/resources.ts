import "server-only";

/**
 * Where `/discover` gets its regions.
 *
 * Two rules shape this module. First, recommendations, watch history, and
 * technical evidence are separate reads: a dead history query must not erase
 * the movie a viewer came for. Second, there is exactly one branch between
 * live data and recorded data, it is gated by the isolated UI mode, and the
 * fixture side goes through `fixture-gate.ts` — which throws rather than
 * returning data when that mode is off, and always throws in production. A
 * live read has no path to a fixture, deliberately.
 */

import type {
  HistoryResponse,
  OnlineUserFeatures,
  RecommendationAuditResponse,
  RecommendationResponse,
} from "@/lib/api";
import type { ApiSession } from "@/lib/bff-auth";
import {
  discoverAudits,
  discoverFeatures,
  discoverHistory,
  emptyHistory,
  emptyRecommendations,
  fallbackAudits,
  fallbackRecommendations,
  learnedRecommendations,
  posterFailureRecommendations,
} from "@/lib/fixtures/discover-fixtures";
import {
  fixtureResourceState,
  fixtureResourcesEnabled,
  injectedResourceFailure,
} from "@/lib/resources/fixture-gate";
import { loadHistory, loadRecommendations } from "@/lib/resources/server";
import { loadingState, type ResourceState } from "@/lib/resources/state";

/**
 * How deep the Discover queue is.
 *
 * The featured slot is a cursor into this set and every decision moves it, so
 * the depth is a headroom question rather than a rail-length one: at ten, ten
 * decisions empty the queue and the viewer hits the end state in a sitting. The
 * endpoint accepts up to fifty; twenty-four is deep enough for a real session
 * and shallow enough to stay inside the page-shaped latency budget 7b measured
 * for `/discover`. The queue extends in the background well before it runs out.
 */
export const DISCOVER_RECOMMENDATION_LIMIT = 24;
export const DISCOVER_HISTORY_LIMIT = 8;

export type RecordedEvidence = {
  audits: ResourceState<RecommendationAuditResponse>;
  features: ResourceState<OnlineUserFeatures>;
};

export type DiscoverResources = {
  recommendations: ResourceState<RecommendationResponse>;
  history: ResourceState<HistoryResponse>;
  /**
   * Present only under the recorded harness. A live route leaves this null and
   * the drawer fetches evidence on demand, so evidence never delays the movie.
   */
  recordedEvidence: RecordedEvidence | null;
};

export const DISCOVER_SCENARIOS = [
  "learned",
  "fallback",
  "empty",
  "loading",
  "auth-expired",
  "recommendations-error",
  "history-error",
  "evidence-error",
  "poster-failure",
] as const;

export type DiscoverScenario = (typeof DISCOVER_SCENARIOS)[number];

/**
 * Parses the recorded-harness selector. It returns a scenario only when the
 * isolated UI mode is on, so a `?demo=` parameter on a real deployment is an
 * ordinary unknown query parameter rather than a lever.
 */
export function discoverScenario(
  value: string | string[] | undefined,
  environment: NodeJS.ProcessEnv = process.env,
): DiscoverScenario | null {
  if (!fixtureResourcesEnabled(environment)) return null;
  const requested = Array.isArray(value) ? value[0] : value;
  // Absent means "read live". Recorded data is never the default, even inside
  // the isolated mode; a reviewer has to ask for it by name.
  if (requested === undefined) return null;
  const scenario = requested.trim() === "" ? "learned" : requested.trim();
  return (DISCOVER_SCENARIOS as readonly string[]).includes(scenario)
    ? (scenario as DiscoverScenario)
    : null;
}

function recordedResources(scenario: DiscoverScenario): DiscoverResources {
  const history = fixtureResourceState("history", discoverHistory);
  const evidence: RecordedEvidence = {
    audits: fixtureResourceState(
      "audits",
      scenario === "fallback" ? fallbackAudits : discoverAudits,
    ),
    features: fixtureResourceState("features", discoverFeatures),
  };

  switch (scenario) {
    case "fallback":
      return {
        recommendations: fixtureResourceState("recommendations", fallbackRecommendations),
        history,
        recordedEvidence: evidence,
      };
    case "empty":
      return {
        recommendations: fixtureResourceState("recommendations", emptyRecommendations, {
          empty: true,
        }),
        history: fixtureResourceState("history", emptyHistory, { empty: true }),
        recordedEvidence: evidence,
      };
    case "loading":
      return {
        recommendations: loadingState("recommendations"),
        history: loadingState("history"),
        recordedEvidence: null,
      };
    case "auth-expired":
      return {
        recommendations: injectedResourceFailure("recommendations", {
          status: "auth-expired",
          reason: "session-expired",
        }),
        history: injectedResourceFailure("history", {
          status: "auth-expired",
          reason: "session-expired",
        }),
        recordedEvidence: null,
      };
    case "recommendations-error":
      return {
        recommendations: injectedResourceFailure("recommendations", {
          status: "upstream-error",
          reason: "timeout",
        }),
        history,
        recordedEvidence: evidence,
      };
    case "history-error":
      return {
        recommendations: fixtureResourceState("recommendations", learnedRecommendations),
        history: injectedResourceFailure("history", {
          status: "upstream-error",
          reason: "server",
        }),
        recordedEvidence: evidence,
      };
    case "evidence-error":
      return {
        recommendations: fixtureResourceState("recommendations", learnedRecommendations),
        history,
        recordedEvidence: {
          audits: injectedResourceFailure("audits", {
            status: "upstream-error",
            reason: "server",
          }),
          features: injectedResourceFailure("features", {
            status: "forbidden",
            reason: "forbidden",
          }),
        },
      };
    case "poster-failure":
      return {
        recommendations: fixtureResourceState(
          "recommendations",
          posterFailureRecommendations,
        ),
        history,
        recordedEvidence: evidence,
      };
    default:
      return {
        recommendations: fixtureResourceState("recommendations", learnedRecommendations),
        history,
        recordedEvidence: evidence,
      };
  }
}

export async function loadDiscoverResources(input: {
  session: ApiSession;
  userId: number;
  requestId?: string | null;
  scenario?: DiscoverScenario | null;
}): Promise<DiscoverResources> {
  if (input.scenario) return recordedResources(input.scenario);

  const options = { session: input.session, requestId: input.requestId };
  // Started together on purpose: history is supporting context and must never
  // sit in front of the first movie.
  const [recommendations, history] = await Promise.all([
    loadRecommendations(input.userId, {
      ...options,
      limit: DISCOVER_RECOMMENDATION_LIMIT,
    }),
    loadHistory(input.userId, { ...options, limit: DISCOVER_HISTORY_LIMIT }),
  ]);
  return { recommendations, history, recordedEvidence: null };
}
