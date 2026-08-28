import { describe, expect, it } from "vitest";

import {
  DEFAULT_FEATURED_PREFERENCE,
  featuredPassOver,
  featuredPreferenceFrom,
  isWatchlisted,
  returningTitlesLine,
  settingNote,
  skipAnnouncement,
  WATCHLIST_SKIPS_BEFORE_NUDGE,
} from "@/lib/discover/featured-preference";
import {
  featuredPreferencesOff,
  featuredPreferencesOn,
} from "@/lib/fixtures/discover-fixtures";
import type { MovieDisplayState } from "@/lib/movie-state/actions";
import { failureState, loadingState, readyState } from "@/lib/resources/state";

const REQUEST_ID = "1a2b3c4d-0000-0000-0000-000000000001";

const state = (overrides: Partial<MovieDisplayState> = {}): MovieDisplayState => ({
  watched: false,
  watchlisted: false,
  dismissed: false,
  rating: null,
  ...overrides,
});

describe("reading the preference", () => {
  it("takes the stored value and the revision the next write asserts", () => {
    expect(
      featuredPreferenceFrom(readyState("preferences", featuredPreferencesOff, REQUEST_ID)),
    ).toEqual({ featureWatchlistedTitles: false, revision: 1 });
  });

  it("falls back to the documented default rather than to an error", () => {
    // The setting chooses between two honest cards; a read that failed must
    // never be the reason no movie is on screen.
    for (const unreadable of [
      loadingState("preferences"),
      failureState({
        status: "upstream-error",
        resource: "preferences",
        reason: "timeout",
        requestId: REQUEST_ID,
      }),
    ]) {
      expect(featuredPreferenceFrom(unreadable)).toEqual(DEFAULT_FEATURED_PREFERENCE);
    }
    expect(DEFAULT_FEATURED_PREFERENCE.featureWatchlistedTitles).toBe(true);
  });

  it("agrees with the API about what an untouched persona is shown", () => {
    expect(
      featuredPreferenceFrom(readyState("preferences", featuredPreferencesOn, REQUEST_ID)),
    ).toEqual(DEFAULT_FEATURED_PREFERENCE);
  });
});

describe("what the featured slot may not show", () => {
  const states = { 101: state({ watchlisted: true }), 102: state({ watched: true }) };

  it("passes over a skipped title whatever the preference says", () => {
    const passOver = featuredPassOver({
      preference: { featureWatchlistedTitles: true, revision: 0 },
      states,
      skipped: [101],
    });

    expect(passOver(101)).toBe(true);
    expect(passOver(102)).toBe(false);
  });

  it("passes over watchlisted titles only when the preference is off", () => {
    const shown = featuredPassOver({
      preference: { featureWatchlistedTitles: true, revision: 1 },
      states,
      skipped: [],
    });
    const held = featuredPassOver({
      preference: { featureWatchlistedTitles: false, revision: 1 },
      states,
      skipped: [],
    });

    expect(shown(101)).toBe(false);
    expect(held(101)).toBe(true);
  });

  it("never passes over a title whose state is unknown", () => {
    // "Not told" is not "not watchlisted". Treating it as watchlisted would
    // silently drop titles from the slot on a page that knows nothing yet.
    const held = featuredPassOver({
      preference: { featureWatchlistedTitles: false, revision: 1 },
      states: {},
      skipped: [],
    });

    expect(held(101)).toBe(false);
    expect(isWatchlisted(undefined)).toBe(false);
    expect(isWatchlisted(state())).toBe(false);
    expect(isWatchlisted(state({ watchlisted: true }))).toBe(true);
  });
});

describe("what the copy is allowed to claim", () => {
  it("reports a skip as a non-event and names where the title still is", () => {
    expect(skipAnnouncement("Heat", "Burning")).toBe(
      "Skipped Heat — still on your watchlist. Next: Burning.",
    );
    expect(skipAnnouncement("Heat", null)).toBe(
      "Skipped Heat — still on your watchlist.",
    );
  });

  it("says watched and dismissed titles never return, in both preference states", () => {
    for (const featureWatchlisted of [true, false]) {
      expect(returningTitlesLine(featureWatchlisted)).toContain(
        "never come back to Discover",
      );
    }
    expect(returningTitlesLine(true)).toContain("do come back, and can be featured");
    expect(returningTitlesLine(false)).toContain("turned off featuring them");
  });

  it("never claims a preference or a skip changed what the model learns", () => {
    const claims = [
      settingNote(true),
      settingNote(false),
      returningTitlesLine(true),
      returningTitlesLine(false),
      skipAnnouncement("Heat", null),
    ].join(" ");

    expect(claims).not.toMatch(/train|learned from|teaches|dislike/i);
    expect(settingNote(true)).toContain("not what the recommender learns");
  });

  it("asks the question at a threshold worth answering", () => {
    expect(WATCHLIST_SKIPS_BEFORE_NUDGE).toBe(3);
  });
});
