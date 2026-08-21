import { describe, expect, it } from "vitest";

import {
  productNavigationItems,
  returnHrefLabel,
  safeReturnHref,
} from "@/lib/navigation";

const FALLBACK = "/browse?user=900000101";

describe("where a movie can send a viewer back to", () => {
  it("honours every collection a movie can be opened from", () => {
    expect(safeReturnHref("/browse?q=heat", FALLBACK)).toBe("/browse?q=heat");
    expect(safeReturnHref("/library?userId=900000101&tab=rated", FALLBACK)).toBe(
      "/library?userId=900000101&tab=rated",
    );
    expect(safeReturnHref("/discover?userId=900000101", FALLBACK)).toBe(
      "/discover?userId=900000101",
    );
  });

  it("discards anything that would leave the product or the origin", () => {
    for (const value of [
      undefined,
      "https://example.com/browse",
      "//example.com/browse",
      "\\\\example.com",
      "/legacy",
      "/browsers",
      "/api/users/1",
    ]) {
      expect(safeReturnHref(value, FALLBACK)).toBe(FALLBACK);
    }
  });

  it("takes the first value when a parameter is repeated", () => {
    expect(safeReturnHref(["/library", "/browse"], FALLBACK)).toBe("/library");
  });

  it("names the destination rather than assuming Browse", () => {
    expect(returnHrefLabel("/browse?user=1")).toBe("Back to Browse");
    expect(returnHrefLabel("/library?tab=history")).toBe("Back to Library");
    expect(returnHrefLabel("/discover?userId=1")).toBe("Back to For you");
    // The recorded preview mirrors the live routes under its own prefix.
    expect(returnHrefLabel("/ui-preview/browse")).toBe("Back to Browse");
    expect(returnHrefLabel("/somewhere-else")).toBe("Back");
  });
});

describe("primary navigation", () => {
  it("carries the selected persona and keeps Quick Picks out of the three slots", () => {
    const items = productNavigationItems(900000101);

    expect(items.map((item) => item.label)).toEqual(["For you", "Browse", "Library"]);
    expect(items.every((item) => item.href.includes("900000101"))).toBe(true);
  });
});
