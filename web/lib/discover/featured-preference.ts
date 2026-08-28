/**
 * Whether a title already on the viewer's watchlist may take Discover's
 * featured slot — and everything the route is allowed to say about it.
 *
 * Three rules hold this together, and they are the reason it is one module
 * rather than a boolean threaded through the component:
 *
 * - **A skip is not a signal.** Passing over the featured title writes nothing:
 *   no watched, no dismissal, no rating, no training negative (ADR 0012). It
 *   changes which card is on screen and nothing else, which is why the copy
 *   says the title is *still* on the watchlist rather than implying a decision
 *   was recorded.
 * - **The preference is presentation, not serving.** The API stores it and no
 *   serving path reads it, so the pass-over happens here, in the client's own
 *   queue. A response's `serving_policy`, its exclusion count, and its audit
 *   row are all unchanged by it — which is exactly what keeps the audit true.
 * - **Only a *known* watchlisted state may be passed over.** A recommendation
 *   response can legitimately carry no state for a title, and "unknown" is not
 *   "not watchlisted": treating it as either would silently drop titles from
 *   the featured slot, or offer a Skip that means nothing. Unknown state means
 *   no cue and no Skip control.
 */

import type { UserPreferences } from "@/lib/api";
import type { MovieDisplayState } from "@/lib/movie-state/actions";
import { hasResourceData, type ResourceState } from "@/lib/resources/state";

export type FeaturedPreference = {
  /** `true` (the default) means a watchlisted title may be featured. */
  featureWatchlistedTitles: boolean;
  /** The revision the next write asserts. `0` means "no stored row yet". */
  revision: number;
};

/**
 * What a persona that has never written a preference gets, matching
 * `DEFAULT_FEATURE_WATCHLISTED_TITLES` in `src/serving/preferences.py`. The
 * owner's decision is that watchlisted titles *do* appear by default and the
 * viewer opts out; the pinned contract test keeps the two ends agreeing.
 */
export const DEFAULT_FEATURED_PREFERENCE: FeaturedPreference = {
  featureWatchlistedTitles: true,
  revision: 0,
};

/**
 * How many watchlisted titles a viewer passes over before being offered the
 * setting. One skip is a passing preference for tonight; three in a session is
 * a pattern worth answering once — and it is offered once, never on every skip
 * after it.
 */
export const WATCHLIST_SKIPS_BEFORE_NUDGE = 3;

export const WATCHLIST_CUE = "On your watchlist";
export const SKIP_LABEL = "Skip";
export const NUDGE_QUESTION = "Stop featuring titles on your watchlist?";
export const NUDGE_CONFIRM = "Stop featuring them";
export const NUDGE_DISMISS = "Keep featuring them";
export const SETTING_LABEL = "Feature watchlisted titles";
export const SETTING_EYEBROW = "Featured picks";

/**
 * Reads the preference out of its resource state.
 *
 * A failed read is the default rather than an error region: the setting decides
 * which of two honest cards is shown first, so a page that could not confirm it
 * should still show a movie. It says nothing about the preference in that case
 * — the toggle reports its own failure when a *write* fails, which is the point
 * at which a viewer is owed an answer.
 */
export function featuredPreferenceFrom(
  state: ResourceState<UserPreferences>,
): FeaturedPreference {
  if (!hasResourceData(state)) return DEFAULT_FEATURED_PREFERENCE;
  return {
    featureWatchlistedTitles: state.data.feature_watchlisted_titles,
    revision: state.data.revision,
  };
}

/**
 * Whether the featured slot may show this title, given what is known about it.
 *
 * `undefined` state is deliberately distinct from an all-false state: the first
 * is "the route has not been told", the second is "the API says nothing is set".
 * Only the second can be passed over.
 */
export function isWatchlisted(state: MovieDisplayState | undefined): boolean {
  return state?.watchlisted === true;
}

/**
 * The featured slot's pass-over rule, as one predicate the queue can apply.
 *
 * Two independent reasons to pass a title over, and they compose rather than
 * override: the viewer skipped this one by hand in this session, or the
 * preference says watchlisted titles do not get the slot. Neither removes the
 * title from the ranked set — it keeps its place in the rail, marked
 * `In watchlist`, because it is still a recommendation.
 */
export function featuredPassOver(input: {
  preference: FeaturedPreference;
  states: Readonly<Record<number, MovieDisplayState>>;
  skipped: readonly number[];
}): (movieId: number) => boolean {
  const skipped = new Set(input.skipped);
  return (movieId: number) => {
    if (skipped.has(movieId)) return true;
    if (input.preference.featureWatchlistedTitles) return false;
    return isWatchlisted(input.states[movieId]);
  };
}

/** What was recorded by a skip: nothing. Said in the viewer's terms. */
export function skipAnnouncement(title: string, next: string | null): string {
  const then = next ? ` Next: ${next}.` : "";
  return `Skipped ${title} — still on your watchlist.${then}`;
}

/**
 * The `Why this?` line about what can and cannot come back.
 *
 * True at the source since Bundle 6 — the exclusion set drops watched and
 * dismissed titles before retrieval — and invisible until now. The second half
 * is the only part the preference changes, and it names which of the two states
 * the viewer is currently in rather than describing the setting in the
 * abstract.
 */
export function returningTitlesLine(featureWatchlistedTitles: boolean): string {
  const never =
    "Movies you have watched or marked “Not for me” never come back to Discover — " +
    "they are excluded before the ranking runs.";
  return featureWatchlistedTitles
    ? `${never} Titles on your watchlist do come back, and can be featured; skipping one changes nothing that was recorded.`
    : `${never} Titles on your watchlist still come back in the ranked list, but you have turned off featuring them.`;
}

/** The one sentence under the setting, in the state it is currently in. */
export function settingNote(featureWatchlistedTitles: boolean): string {
  return featureWatchlistedTitles
    ? "Watchlisted titles can take the featured slot. This changes what you are shown, not what the recommender learns."
    : "Watchlisted titles stay in the ranked list but never take the featured slot. This changes what you are shown, not what the recommender learns.";
}
