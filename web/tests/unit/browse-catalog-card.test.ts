import { describe, expect, it } from "vitest";

import type { CatalogItem } from "@/lib/api";
import {
  catalogItemToCard,
  metadataNote,
  overviewText,
  stateNote,
} from "@/lib/browse/catalog-card";

function item(overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    movie_id: 1,
    title: "Toy Story (1995)",
    genres: ["Animation", "Children"],
    tmdb_id: "862",
    release_year: 1995,
    poster_url: "/posters/toy-story.svg",
    overview: "Toys with opinions.",
    metadata_source: "reviewed-fixture",
    source_status: "complete",
    state: null,
    interaction_count: 12,
    ...overrides,
  };
}

describe("catalogItemToCard", () => {
  it("hands the card a title without the year the grid prints anyway", () => {
    expect(catalogItemToCard(item()).title).toBe("Toy Story");
  });

  it("leaves a trailing parenthetical that is not the structured year", () => {
    expect(catalogItemToCard(item({ title: "Se7en (1995)", release_year: 1997 })).title).toBe(
      "Se7en (1995)",
    );
  });

  it("keeps the raw title when there is no structured year to compare", () => {
    expect(catalogItemToCard(item({ release_year: null })).title).toBe("Toy Story (1995)");
  });

  it("keeps the poster decorative beside the visible title", () => {
    expect(catalogItemToCard(item()).posterAlt).toBe("");
  });
});

describe("metadata fallbacks", () => {
  it("explains an incomplete record and stays quiet about a complete one", () => {
    expect(metadataNote(item())).toBeNull();
    expect(metadataNote(item({ source_status: "partial" }))).toBe("Partial details");
    expect(overviewText(item({ overview: null, source_status: "unavailable" }))).toContain(
      "There is no synopsis to show",
    );
  });

  it("says what state a row is in without implying a model claim", () => {
    expect(stateNote(null)).toBeNull();
    expect(
      stateNote({
        tenant_id: "demo",
        user_id: 1,
        movie_id: 1,
        rating: 4,
        rating_updated_at: null,
        watched_at: "2026-08-01T00:00:00Z",
        watchlisted_at: null,
        dismissed_at: null,
        revision: 2,
        updated_at: "2026-08-01T00:00:00Z",
      }),
    ).toBe("Rated 4.0");
  });
});
