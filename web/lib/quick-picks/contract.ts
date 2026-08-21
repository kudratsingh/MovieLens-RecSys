/**
 * What a Quick Picks decision means — to the product and to the model.
 *
 * ADR 0012 pins four different meanings onto four buttons that look alike, and
 * the difference between them is invisible in the UI unless it is written down
 * somewhere a test can read. That is what this module is: the semantics table
 * is the assertable statement of "watchlist changes no model input", "only a
 * committed watched signal moves cold-start progress", and "a dismissal
 * excludes without ever becoming a negative label".
 *
 * Nothing here touches the network or the DOM, so the contract tests can hold
 * it to those claims directly.
 */

import type { RecommendationItem, RecommendationResponse, ServingPolicy } from "@/lib/api";
import { displayTitle } from "@/lib/discover/movie-card";

/**
 * Queue depth. Deep enough that a viewer can make several decisions without a
 * round trip, shallow enough that the exclusions applied server-side stay close
 * to what is on screen.
 */
export const QUICK_PICK_QUEUE_LIMIT = 12;

/** Audit rows to scan for the one matching this queue's correlation ID. */
export const QUICK_PICK_AUDIT_LOOKBACK = 3;

export type QuickPickActionKind =
  | "watchlist"
  | "watched"
  | "dismiss"
  | "undo-dismiss";

/** The `user_movie_state` sub-resource a commit writes. */
export type QuickPickResource = "watchlist" | "watched" | "rating" | "dismissal";

export type QuickPickCard = {
  movieId: number;
  title: string;
  year: number | null;
  genres: readonly string[];
  overview: string | null;
  posterUrl: string | null;
  /** Backend-authored reason string; never a client invention. */
  reason: string;
  metadataSource: RecommendationItem["metadata_source"];
};

export type QuickPickSemantics = {
  label: string;
  /** ADR 0012 §4: only a committed watched signal is a positive interaction. */
  advancesPositiveProgress: boolean;
  /** Whether the action removes the title from future serving. */
  excludesFromServing: boolean;
  undoable: boolean;
  /** Shown to the viewer, and the sentence the contract tests hold us to. */
  modelEffect: string;
};

export const QUICK_PICK_SEMANTICS: Record<QuickPickActionKind, QuickPickSemantics> =
  {
    watchlist: {
      label: "Watchlist",
      advancesPositiveProgress: false,
      excludesFromServing: false,
      undoable: false,
      modelEffect:
        "Saved for later. The watchlist is organizational and changes no model input.",
    },
    watched: {
      label: "Watched",
      advancesPositiveProgress: true,
      excludesFromServing: true,
      undoable: false,
      modelEffect:
        "Recorded as one positive watched interaction. Star magnitude is display feedback, not a model weight.",
    },
    dismiss: {
      label: "Not for me",
      advancesPositiveProgress: false,
      excludesFromServing: true,
      undoable: true,
      modelEffect:
        "Excluded from future picks. It never becomes a negative training label.",
    },
    "undo-dismiss": {
      label: "Undo",
      advancesPositiveProgress: false,
      excludesFromServing: false,
      undoable: false,
      modelEffect: "Dismissal cleared. The title is eligible for picks again.",
    },
  };

export type QuickPickCommitRequest = {
  action: QuickPickActionKind;
  movieId: number;
  /** Only meaningful for `watched`; ignored elsewhere. */
  rating: number | null;
  /**
   * The revision the machine actually observed, or `null` for a queue card —
   * a recommendation carries no state, and inventing one here would be a claim
   * about the server. The transport answers for `null` from what this session
   * and other routes have committed.
   */
  expectedRevision: number | null;
};

export type QuickPickHttpRequest = {
  resource: QuickPickResource;
  method: "PUT" | "DELETE";
  body: { rating: number } | null;
  expectedRevision: number | null;
};

/** Half-star values from 0.5 through 5.0, matching the API and DB constraint. */
export function isServableRating(value: number): boolean {
  return (
    Number.isFinite(value) &&
    value >= 0.5 &&
    value <= 5 &&
    Number.isInteger(value * 2)
  );
}

export class InvalidQuickPickRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidQuickPickRequestError";
  }
}

/**
 * Translate one decision into one HTTP mutation.
 *
 * `watched` with a rating goes to the rating resource on purpose: the API
 * treats a rating as implying watched, so a single mutation produces one
 * canonical state and one revision instead of two writes the UI would have to
 * reconcile in order.
 */
export function quickPickHttpRequest(
  request: QuickPickCommitRequest,
): QuickPickHttpRequest {
  const expectedRevision = request.expectedRevision;
  switch (request.action) {
    case "watchlist":
      return { resource: "watchlist", method: "PUT", body: null, expectedRevision };
    case "watched": {
      if (request.rating === null) {
        return { resource: "watched", method: "PUT", body: null, expectedRevision };
      }
      if (!isServableRating(request.rating)) {
        throw new InvalidQuickPickRequestError(
          `Rating ${request.rating} is not a half-star value between 0.5 and 5.`,
        );
      }
      return {
        resource: "rating",
        method: "PUT",
        body: { rating: request.rating },
        expectedRevision,
      };
    }
    case "dismiss":
      return { resource: "dismissal", method: "PUT", body: null, expectedRevision };
    case "undo-dismiss":
      return {
        resource: "dismissal",
        method: "DELETE",
        body: null,
        expectedRevision,
      };
  }
}

export function toQuickPickCard(item: RecommendationItem): QuickPickCard {
  return {
    movieId: item.movie_id,
    // MovieLens embeds the year in the title; the shared helper drops it only
    // when the year is also available as structured metadata, so the card does
    // not read "Heat (1995)" above a "1995" line.
    title: displayTitle(item.title, item.release_year),
    year: item.release_year,
    genres: item.genres,
    overview: item.overview,
    posterUrl: item.poster_url,
    reason: item.reason,
    metadataSource: item.metadata_source,
  };
}

export function toQuickPickQueue(
  response: RecommendationResponse,
): readonly QuickPickCard[] {
  return response.items.map(toQuickPickCard);
}

export type QuickPickProgress = {
  count: number;
  threshold: number;
  /** Only ever true because a returned policy said so. */
  learned: boolean;
  remaining: number;
  /** Enough signals are recorded; it does not mean serving has switched. */
  thresholdReached: boolean;
  policyName: string;
};

/**
 * `committedSinceLoad` exists because the policy count is only as fresh as the
 * last recommendations response. Counting locally committed watched signals on
 * top of it is what lets progress move the moment the API confirms a write,
 * without ever inventing a policy transition the API has not reported.
 */
export function quickPickProgress(
  policy: ServingPolicy | null,
  committedSinceLoad: number,
): QuickPickProgress {
  const threshold = policy?.threshold ?? 5;
  const count = (policy?.positive_signal_count ?? 0) + committedSinceLoad;
  return {
    count,
    threshold,
    learned: policy?.learned ?? false,
    remaining: Math.max(threshold - count, 0),
    thresholdReached: count >= threshold,
    policyName: policy?.name ?? "unknown",
  };
}

export const FALLBACK_POLICY_COPY = "Popular while we learn";
export const LEARNED_POLICY_COPY = "Picked from your watched history";

/**
 * Policy copy branches on the `learned` flag and never on the policy name: the
 * name is a serving implementation detail that has already changed once.
 */
export function policyHeadline(policy: ServingPolicy | null): string {
  return policy?.learned ? LEARNED_POLICY_COPY : FALLBACK_POLICY_COPY;
}
