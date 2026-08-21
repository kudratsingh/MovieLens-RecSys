/**
 * How a Quick Picks decision reaches the durable boundary.
 *
 * Mutations go through the Bundle 2 BFF feedback routes unchanged — same
 * same-origin rules, same Auth.js CSRF token, same idempotency key, same
 * canonical response — because forking a second mutation path is exactly how
 * two surfaces end up disagreeing about what "watched" means. The queue read
 * arrives as an injected function so the live route can hand over a server
 * action while the fixture harness hands over recorded data.
 */

import type { MovieState, RecommendationResponse } from "@/lib/api";
import {
  quickPickHttpRequest,
  type QuickPickCommitRequest,
} from "@/lib/quick-picks/contract";
import type { QuickPickEvidenceMap } from "@/lib/quick-picks/evidence";
import type { ResourceState } from "@/lib/resources/state";
import { isFeedbackMutationResponse } from "@/lib/resources/validate";

export type QuickPickCommitOutcome =
  | { ok: true; state: MovieState }
  | { ok: false; message: string };

/**
 * The queue and the audit that explains it travel together: they are read in
 * one server round trip so the evidence can be matched to the queue by
 * correlation ID instead of by hoping the two reads lined up.
 */
export type QuickPickQueuePayload = {
  queue: ResourceState<RecommendationResponse>;
  evidence: QuickPickEvidenceMap;
};

export type QuickPickTransport = {
  commit(request: QuickPickCommitRequest): Promise<QuickPickCommitOutcome>;
  refresh(): Promise<QuickPickQueuePayload>;
  /** `null` whenever the seed title cannot be established truthfully. */
  resolveSeedTitle(movieId: number): Promise<string | null>;
};

const COMMIT_FAILED = "The recommendation API did not save that decision.";

async function csrfToken(fetchImpl: typeof fetch): Promise<string> {
  const response = await fetchImpl("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not create a secure feedback request.");
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}

function detailOf(payload: unknown): string | null {
  if (typeof payload !== "object" || payload === null) return null;
  const detail = (payload as { detail?: unknown }).detail;
  return typeof detail === "string" && detail.length > 0 ? detail : null;
}

export function createLiveQuickPickTransport(options: {
  userId: number;
  loadQueue: () => Promise<QuickPickQueuePayload>;
  loadSeedTitle: (movieId: number) => Promise<string | null>;
  fetchImpl?: typeof fetch;
}): QuickPickTransport {
  const fetchImpl = options.fetchImpl ?? fetch;

  return {
    async commit(request) {
      try {
        const http = quickPickHttpRequest(request);
        const query =
          http.expectedRevision === null
            ? ""
            : `?expected_revision=${encodeURIComponent(http.expectedRevision)}`;
        const response = await fetchImpl(
          `/api/users/${options.userId}/movies/${request.movieId}/${http.resource}${query}`,
          {
            method: http.method,
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": crypto.randomUUID(),
              "x-csrf-token": await csrfToken(fetchImpl),
            },
            body: http.body ? JSON.stringify(http.body) : undefined,
          },
        );
        const payload: unknown = await response.json().catch(() => null);
        if (!response.ok || !isFeedbackMutationResponse(payload)) {
          return { ok: false, message: detailOf(payload) ?? COMMIT_FAILED };
        }
        return { ok: true, state: payload.state };
      } catch (error) {
        return {
          ok: false,
          message: error instanceof Error ? error.message : COMMIT_FAILED,
        };
      }
    },

    refresh: options.loadQueue,

    async resolveSeedTitle(movieId) {
      try {
        return await options.loadSeedTitle(movieId);
      } catch {
        // Evidence is progressive disclosure; losing a seed title degrades the
        // sentence to its source rather than failing the card.
        return null;
      }
    },
  };
}
