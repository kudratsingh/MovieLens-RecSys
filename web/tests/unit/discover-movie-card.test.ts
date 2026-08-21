import { describe, expect, it } from "vitest";

import {
  displayTitle,
  recommendationCard,
  recommendationCards,
} from "@/lib/discover/movie-card";
import { learnedRecommendations } from "@/lib/fixtures/discover-fixtures";
import { UNKNOWN_MOVIE_STATE } from "@/lib/movie-state/actions";

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
});
