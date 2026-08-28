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
  UserPreferences,
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
  featuredPreferencesOff,
  featuredPreferencesOn,
  learnedRecommendations,
  posterFailureRecommendations,
  watchlistedRecommendations,
} from "@/lib/fixtures/discover-fixtures";
import {
  fixtureResourceState,
  fixtureResourcesEnabled,
  injectedResourceFailure,
} from "@/lib/resources/fixture-gate";
import {
  loadHistory,
  loadRecommendations,
  loadUserPreferences,
} from "@/lib/resources/server";
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
   * The `Featured picks` setting. Read alongside the ranked set because it
   * decides which of its titles leads — but never gated on: a failed read is
   * the documented default, not an error region, because the setting chooses
   * between two honest cards rather than deciding whether there is one.
   */
  preferences: ResourceState<UserPreferences>;
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
  // The two `Featured picks` states: a watchlisted title leading the ranked set
  // with a Skip beside it, and the same set with the preference turned off so
  // the slot passes those titles over and the rail keeps them.
  "watchlisted",
  "watchlist-held-back",
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

type RecordedRegions = Omit<DiscoverResources, "preferences">;

/**
 * The recorded preference for a scenario. Every scenario carries one, because
 * the route always has a `Featured picks` setting — the only question is which
 * of its two states is being recorded.
 */
function recordedPreference(scenario: DiscoverScenario) {
  return fixtureResourceState(
    "preferences",
    scenario === "watchlist-held-back" ? featuredPreferencesOff : featuredPreferencesOn,
  );
}

function recordedResources(scenario: DiscoverScenario): DiscoverResources {
  return {
    ...recordedRegions(scenario),
    preferences: recordedPreference(scenario),
  };
}

function recordedRegions(scenario: DiscoverScenario): RecordedRegions {
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
    // One recorded set serves both `Featured picks` states; the preference is
    // what differs, and it is applied above. Recording two nearly identical
    // ranked sets would only invite them to drift.
    case "watchlisted":
    case "watchlist-held-back":
      return {
        recommendations: fixtureResourceState(
          "recommendations",
          watchlistedRecommendations,
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
  // Started together on purpose: history is supporting context and the setting
  // is a small read, and neither may sit in front of the first movie.
  const [recommendations, history, preferences] = await Promise.all([
    loadRecommendations(input.userId, {
      ...options,
      limit: DISCOVER_RECOMMENDATION_LIMIT,
    }),
    loadHistory(input.userId, { ...options, limit: DISCOVER_HISTORY_LIMIT }),
    // Started with them, not after them: it decides which of the ranked titles
    // leads, so a serial read would put a settings lookup in front of the first
    // movie. A failure here is the documented default, never an error region.
    loadUserPreferences(input.userId, options),
  ]);
  return { recommendations, history, preferences, recordedEvidence: null };
}
