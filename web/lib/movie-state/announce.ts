/**
 * What the product says after a movie-state write.
 *
 * Three routes used to keep three vocabularies for the same four events, which
 * is how a watchlist entry ended up described as organizational in one place
 * and as nothing in particular in another. The vocabulary is one table now.
 *
 * It still has three *voices*, and that is deliberate rather than a leftover
 * fork. A recommendation card is a passing surface and says the short thing; a
 * movie's own page has room to say what a star actually commits to; the Library
 * is a record kept on behalf of a named persona and says whose record changed.
 * Keeping the voices in one table is what makes the differences reviewable —
 * and what makes it obvious if one of them ever starts claiming more than the
 * deployed system does.
 *
 * Every line is held to ADR 0012: a rating records watched history but is not a
 * graded signal, watchlist changes no model input, dismissal is an undoable
 * exclusion rather than a learned negative.
 */

import type { MovieStateAction } from "@/lib/movie-state/actions";
import type { ResourceFailure, ResourceFailureReason } from "@/lib/resources/state";

export type MovieStateVoice = "detail" | "discover" | "library";

export type MovieStateOutcome =
  | { kind: "committed"; action: MovieStateAction }
  | { kind: "conflict" }
  /**
   * The API declined the transition and said why; `detail` is its sentence.
   * `corrected` is set when the re-read that followed actually moved the
   * control — a reader who cannot see it change has no other way to learn it
   * did, and a silent correction is the accessibility problem this whole
   * region exists to avoid.
   */
  | { kind: "refused"; detail: string; corrected?: boolean }
  | { kind: "failed"; failure: ResourceFailure };

export type AnnouncementContext = {
  title: string;
  voice: MovieStateVoice;
  /** Required by the Library voice, which names whose record changed. */
  persona?: string;
  /**
   * The title now in the featured slot, for the surfaces that move on after a
   * decision. Only the Discover voice says it: there, the decision and the
   * advance are one event, and a reader who cannot see the card move has no
   * other way to learn it did.
   */
  next?: string | null;
};

export function movieStateAnnouncement(
  outcome: MovieStateOutcome,
  context: AnnouncementContext,
): string {
  if (outcome.kind === "committed") {
    return committedLine(outcome.action, context);
  }
  if (outcome.kind === "conflict") {
    return conflictLine(context);
  }
  if (outcome.kind === "refused") {
    return refusedLine(outcome.detail, context, outcome.corrected ?? false);
  }
  return failureLine(outcome.failure, context);
}

function committedLine(
  action: MovieStateAction,
  { title, voice, persona, next }: AnnouncementContext,
): string {
  const saved = action.method === "PUT";
  switch (voice) {
    case "discover": {
      // Said after the decision, so the sentence reads in the order the events
      // happened: what was recorded, then what is now on screen.
      const then = next ? ` Next: ${next}.` : "";
      switch (action.resource) {
        case "rating":
          // A star is recorded here from the follow-up panel, on a title that
          // is already watched — so the sentence has to answer the question the
          // panel leaves behind ("did that change the list?") rather than
          // stop at "saved". It says the ADR 0012 truth in the viewer's terms:
          // the watch is the signal, the star is not.
          return saved
            ? `Rated ${title} ${action.rating}/5. Ratings do not reorder the list — the watch already counts.${then}`
            : `Rating removed from ${title}; watched history was preserved.${then}`;
        case "watched":
          return saved
            ? `${title} marked watched.${then}`
            : `${title} removed from watched history.${then}`;
        case "watchlist":
          return saved
            ? `${title} saved to watchlist.${then}`
            : `${title} removed from watchlist.${then}`;
        case "dismissal":
          return saved
            ? `${title} will be excluded from recommendations.${then}`
            : `${title} is eligible again.${then}`;
      }
      break;
    }

    case "library": {
      const owner = `${persona ?? "this persona"}'s`;
      switch (action.resource) {
        case "rating":
          return saved
            ? `Rating saved for ${title} in ${owner} library. It stays watched history; the star value is display feedback only.`
            : `Rating removed from ${title} in ${owner} library. It is still watched history.`;
        case "watched":
          return saved
            ? `${title} marked watched in ${owner} library.`
            : `${title} removed from ${owner} watched history, along with its rating.`;
        case "watchlist":
          return saved
            ? `${title} saved to ${owner} watchlist. Saving does not change recommendations.`
            : `${title} removed from ${owner} watchlist.`;
        case "dismissal":
          return saved
            ? `${title} excluded from ${owner} recommendations. This can be undone.`
            : `${title} is eligible for ${owner} recommendations again.`;
      }
      break;
    }

    case "detail":
      switch (action.resource) {
        case "rating":
          // "Rating saved." leads here for the same reason, and the Library
          // journey waits on this exact prefix on the detail route.
          return saved
            ? `Rating saved. ${action.rating} ${action.rating === 1 ? "star" : "stars"} for ${title} — a rating records watched history, and star magnitude is not a graded model signal.`
            : `Rating removed. ${title} stays in watched history.`;
        case "watched":
          return saved
            ? `Marked ${title} as watched. It now counts in live history and unseen filtering.`
            : `Removed ${title} from history, including the watched interaction.`;
        case "watchlist":
          return saved
            ? `Saved ${title} to your watchlist. Watchlist is organisational and changes no recommendation input.`
            : `Removed ${title} from your watchlist.`;
        case "dismissal":
          return saved
            ? `Dismissed ${title}. It is excluded from recommendations and can be undone.`
            : `Restored ${title}. It can be recommended again.`;
      }
      break;
  }
  // Unreachable: every voice covers every resource above.
  return `${title} was updated.`;
}

/**
 * A conflict is not a broken request. Somebody else — another tab, another
 * device, another route in this same session — committed first, and every
 * surface answers it the same way: re-read the canonical record and show it.
 */
function conflictLine({ title, voice, persona }: AnnouncementContext): string {
  if (voice === "library") {
    return `${title} changed elsewhere. ${persona ?? "This persona"}'s latest saved state is shown.`;
  }
  return `${title} changed somewhere else before this saved. Its current state has been loaded; try again.`;
}

/**
 * A refusal is not a conflict and not a failure. Nothing was stored, the
 * request was understood, and the API named the rule it would have broken — so
 * that sentence is repeated verbatim rather than translated. The lead-in says
 * the one thing the API's sentence does not: which title this was about, and
 * that it was left alone. No retry is offered, because there is nothing here
 * that a second press would change.
 *
 * `corrected` adds the one clause a refusal owes when the control moved under
 * the reader: the rule that was broken is a rule about state, so the write path
 * re-reads and the card may now show something else. Saying "not changed" and
 * then quietly changing what is on screen is the version of this that lies.
 */
function refusedLine(
  detail: string,
  { title, voice, persona }: AnnouncementContext,
  corrected: boolean,
): string {
  const reason = sentence(detail);
  const shown = corrected ? " Its current state is shown." : "";
  if (voice === "library") {
    return `${title} was left as it is in ${persona ?? "this persona"}'s library. ${reason}${shown}`;
  }
  return `${title} was not changed. ${reason}${shown}`;
}

/** The API's details are lowercase fragments with no terminator of their own. */
function sentence(detail: string): string {
  const text = detail.trim();
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

function failureLine(
  failure: ResourceFailure,
  { title, voice, persona }: AnnouncementContext,
): string {
  if (voice === "library") {
    // The Library shows the cause in its own alert, so the live region says the
    // one thing a reader cannot see: the collection was put back.
    return `Could not update ${title}. ${persona ?? "This persona"}'s saved state was restored.`;
  }

  switch (failure.status) {
    case "auth-expired":
      return `Your session expired before this saved. Sign in again to change ${title}.`;
    case "forbidden":
      return `This session is not allowed to change state for ${title}.`;
    case "not-found":
      return `${title} is no longer in the catalog for this persona.`;
    default: {
      if (voice === "discover") {
        // A card has room for the outcome and the correlation ID, not a cause.
        return `${title} was not saved. It was left as it was. Request ${failure.requestId}.`;
      }
      const cause = TRANSPORT_CAUSE[failure.reason];
      // Without a cause we can state plainly, the request ID is the only useful
      // thing to hand over — it is what ties this to an audit row.
      return cause
        ? `${cause} Nothing was saved for ${title}.`
        : `Nothing was saved for ${title}. Request ${failure.requestId}.`;
    }
  }
}

const TRANSPORT_CAUSE: Partial<Record<ResourceFailureReason, string>> = {
  timeout: "The recommendation API did not answer.",
  network: "The recommendation API could not be reached.",
  server: "The recommendation API returned an error.",
  "rate-limited": "Too many requests reached the recommendation API.",
};
