import { describe, expect, it } from "vitest";

import type { MovieState } from "@/lib/api";
import {
  UNKNOWN_MOVIE_STATE,
  displayTitle,
  movieCardState,
  optimisticCardState,
  recommendationCard,
  recommendationCards,
} from "@/lib/discover/movie-card";
import { learnedRecommendations } from "@/lib/fixtures/discover-fixtures";

const canonical: MovieState = {
  movie_id: 101,
  user_id: 900000101,
  tenant_id: "demo",
  revision: 4,
  updated_at: "2026-08-21T09:00:00Z",
  rating: 4,
  rating_updated_at: "2026-08-21T09:00:00Z",
  watched_at: "2026-08-21T09:00:00Z",
  watchlisted_at: null,
  dismissed_at: null,
};

describe("recommendation cards", () => {
  it("drops the MovieLens year from the title only when it is also structured", () => {
    expect(displayTitle("Heat (1995)", 1995)).toBe("Heat");
    expect(displayTitle("Heat (1995)", null)).toBe("Heat (1995)");
    expect(displayTitle("Heat (1995)", 1996)).toBe("Heat (1995)");
    expect(displayTitle("2001: A Space Odyssey", 1968)).toBe("2001: A Space Odyssey");
  });

  it("starts from unknown state because recommendations carry none", () => {
    const card = recommendationCard(learnedRecommendations.items[0], 1);

    expect(card.state).toEqual(UNKNOWN_MOVIE_STATE);
    expect(card.rank).toBe(1);
    expect(card.reason).toBe(learnedRecommendations.items[0].reason);
  });

  it("leaves the poster alt empty only when there is no poster to describe", () => {
    const withArt = recommendationCard(learnedRecommendations.items[0], 1);
    const withoutArt = recommendationCard(
      { ...learnedRecommendations.items[0], poster_url: null },
      1,
    );

    expect(withArt.posterAlt).toBe(`Poster for ${withArt.title}`);
    expect(withoutArt.posterAlt).toBe("");
  });

  it("overlays only the movies this session has committed state for", () => {
    const cards = recommendationCards(learnedRecommendations.items, {
      [learnedRecommendations.items[1].movie_id]: {
        ...UNKNOWN_MOVIE_STATE,
        watchlisted: true,
      },
    });

    expect(cards[0].state).toEqual(UNKNOWN_MOVIE_STATE);
    expect(cards[1].state.watchlisted).toBe(true);
  });

  it("derives display state from the canonical timestamps", () => {
    expect(movieCardState(canonical)).toEqual({
      watched: true,
      watchlisted: false,
      rating: 4,
      suppressed: false,
    });
    expect(movieCardState(null)).toEqual(UNKNOWN_MOVIE_STATE);
  });
});

describe("optimistic transitions follow the pinned feedback semantics", () => {
  it("clears the watchlist when a movie becomes watched", () => {
    const saved = { ...UNKNOWN_MOVIE_STATE, watchlisted: true };

    expect(optimisticCardState(saved, "watched", "PUT")).toEqual({
      ...UNKNOWN_MOVIE_STATE,
      watched: true,
      watchlisted: false,
    });
  });

  it("implies watched when a rating is set and preserves watched when it is removed", () => {
    const rated = optimisticCardState(UNKNOWN_MOVIE_STATE, "rating", "PUT", 5);
    expect(rated).toMatchObject({ watched: true, rating: 5 });

    expect(optimisticCardState(rated, "rating", "DELETE")).toMatchObject({
      watched: true,
      rating: null,
    });
  });

  it("treats dismissal as an exclusion that also drops the watchlist entry", () => {
    const saved = { ...UNKNOWN_MOVIE_STATE, watchlisted: true };

    expect(optimisticCardState(saved, "dismissal", "PUT")).toMatchObject({
      suppressed: true,
      watchlisted: false,
    });
    expect(
      optimisticCardState({ ...saved, suppressed: true }, "dismissal", "DELETE"),
    ).toMatchObject({ suppressed: false });
  });

  it("keeps watchlist organizational: it changes nothing else", () => {
    const watched = { ...UNKNOWN_MOVIE_STATE, watched: true, rating: 3 };

    expect(optimisticCardState(watched, "watchlist", "PUT")).toEqual({
      ...watched,
      watchlisted: true,
    });
  });
});
