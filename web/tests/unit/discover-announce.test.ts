import { describe, expect, it } from "vitest";

import { discoverMutationAnnouncement } from "@/lib/discover/announce";

describe("Discover announcements keep each state's meaning distinct", () => {
  it("says what a rating deletion does and does not do", () => {
    expect(discoverMutationAnnouncement("rating", "DELETE", "Heat")).toContain(
      "watched history was preserved",
    );
  });

  it("describes dismissal as an exclusion rather than a dislike", () => {
    expect(discoverMutationAnnouncement("dismissal", "PUT", "Heat")).toBe(
      "Heat will be excluded from recommendations.",
    );
  });

  it("keeps watchlist organizational", () => {
    expect(discoverMutationAnnouncement("watchlist", "PUT", "Heat")).toBe(
      "Heat saved to watchlist.",
    );
  });

  it("leads a saved rating with the phrase the journey waits on", () => {
    expect(discoverMutationAnnouncement("rating", "PUT", "Heat")).toBe(
      "Rating saved for Heat.",
    );
  });
});
