import { describe, expect, it } from "vitest";

import {
  frontDoorHref,
  productNavigationItems,
  returnHrefLabel,
  routeReturnHref,
  safeReturnHref,
  safeSignInReturn,
  signInDestination,
  signInHref,
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

  it("keeps a movie out of the back-link allow-list", () => {
    // Detail's back link is labelled from `RETURN_LABELS`, and "back" to
    // another movie is not a destination it can name. The sign-in door has its
    // own, wider list for exactly this reason.
    expect(safeReturnHref("/movies/296?user=900000103", FALLBACK)).toBe(FALLBACK);
    expect(safeSignInReturn("/movies/296?user=900000103")).toBe(
      "/movies/296?user=900000103",
    );
  });
});

describe("the address the sign-in door returns to", () => {
  it("keeps every product address a signed-out visitor can be bounced off", () => {
    for (const value of [
      "/discover?userId=900000102",
      "/browse?user=900000103&q=heat",
      "/library?userId=900000102&tab=watchlist",
      "/movies/296?user=900000103",
      "/quick-picks?user=900000104",
      "/legacy",
    ]) {
      expect(safeSignInReturn(value)).toBe(value);
    }
  });

  it("discards anything that would leave the origin or the product", () => {
    for (const value of [
      undefined,
      null,
      "",
      "https://evil.example/discover",
      "//evil.example",
      "/\\evil.example",
      "\\\\evil.example",
      "/admin",
      "/api/users/900000101/ratings",
      "discover",
      // Long enough that it is not one of our addresses.
      `/library?userId=900000101&cursor=${"a".repeat(600)}`,
    ]) {
      expect(safeSignInReturn(value)).toBeNull();
    }
  });

  it("refuses a value carrying a control character", () => {
    // A newline in a redirect target is a header- and log-injection primitive,
    // and it can reach here from a hand-written link.
    expect(safeSignInReturn("/discover?userId=1\nSet-Cookie: a=b")).toBeNull();
    expect(safeSignInReturn("/browse\u0000")).toBeNull();
  });

  it("takes the first value when the parameter is repeated", () => {
    expect(safeSignInReturn(["/library?tab=history", "/browse"])).toBe(
      "/library?tab=history",
    );
  });
});

describe("the door a protected route redirects to", () => {
  it("carries the requested address, encoded", () => {
    expect(signInHref("/library?userId=900000102&tab=watchlist")).toBe(
      `/?next=${encodeURIComponent("/library?userId=900000102&tab=watchlist")}`,
    );
    expect(signInHref("/movies/296?user=900000103")).toBe(
      `/?next=${encodeURIComponent("/movies/296?user=900000103")}`,
    );
  });

  it("falls back to the bare door rather than passing something it cannot honour", () => {
    expect(signInHref("https://evil.example/discover")).toBe("/");
    expect(signInHref("/admin")).toBe("/");
  });

  it("names the destination so the door can promise it", () => {
    expect(signInDestination("/movies/296?user=1")).toBe("that movie");
    expect(signInDestination("/library?tab=watchlist")).toBe("Library");
    expect(signInDestination("/quick-picks?user=1")).toBe("Quick picks");
    expect(signInDestination("/discover?userId=1")).toBe("For you");
    expect(signInDestination("/admin")).toBeNull();
  });
});

describe("rebuilding the address a route was asked for", () => {
  it("keeps every parameter the route was given", () => {
    expect(
      routeReturnHref("/library", {
        userId: "900000102",
        tab: "watchlist",
        sort: "title",
        q: "heat",
      }),
    ).toBe("/library?userId=900000102&tab=watchlist&sort=title&q=heat");
  });

  it("takes the first value of a repeated parameter and drops empty ones", () => {
    expect(routeReturnHref("/browse", { user: ["900000103", "1"], q: "" })).toBe(
      "/browse?user=900000103",
    );
  });

  it("returns a bare path when there is nothing to carry", () => {
    expect(routeReturnHref("/quick-picks", {})).toBe("/quick-picks");
    expect(routeReturnHref("/quick-picks", { user: undefined })).toBe("/quick-picks");
  });

  it("encodes a separator a caller tried to smuggle into a value", () => {
    // The result still has to survive `safeSignInReturn`, and an escaped value
    // is what lets it: an unescaped `//` would be rejected outright.
    const href = routeReturnHref("/browse", { q: "//evil.example" });

    expect(href).toBe("/browse?q=%2F%2Fevil.example");
    expect(safeSignInReturn(href)).toBe(href);
  });
});

describe("primary navigation", () => {
  it("carries the selected persona and keeps Quick Picks out of the three slots", () => {
    const items = productNavigationItems(900000101);

    expect(items.map((item) => item.label)).toEqual(["For you", "Browse", "Library"]);
    expect(items.every((item) => item.href.includes("900000101"))).toBe(true);
  });

  it("points For you at /discover rather than at the front door", () => {
    // The pre-cutover landing route linked `Discover` to itself, which is why
    // /discover had no inbound link from anywhere.
    const [forYou] = productNavigationItems(900000101);

    expect(forYou.href.startsWith("/discover?")).toBe(true);
    expect(forYou.match).toBe("/discover");
  });
});

describe("the front door", () => {
  it("sends a signed-in viewer to the product, not to the dashboard", () => {
    expect(frontDoorHref({})).toBe("/discover?userId=900000101");
  });

  it("keeps the persona a link carried, under either spelling", () => {
    expect(frontDoorHref({ userId: "900000104" })).toBe("/discover?userId=900000104");
    // Browse and movie detail spell it `user`; a link either of them produced
    // must not quietly land on the default persona.
    expect(frontDoorHref({ user: "900000103" })).toBe("/discover?userId=900000103");
    expect(frontDoorHref({ user: "900000103", userId: "900000102" })).toBe(
      "/discover?userId=900000102",
    );
  });

  it("falls back to the default persona rather than trusting the address", () => {
    for (const value of ["", "abc", "-1", "0", "9e9"]) {
      expect(frontDoorHref({ userId: value })).toBe("/discover?userId=900000101");
    }
  });

  it("prefers a validated next over the default product address", () => {
    expect(frontDoorHref({ next: "/movies/296?user=900000103" })).toBe(
      "/movies/296?user=900000103",
    );
    // The requested address carries its own persona, so a stale `userId`
    // alongside it does not get to override the place the viewer asked for.
    expect(
      frontDoorHref({ userId: "900000101", next: "/library?userId=900000102" }),
    ).toBe("/library?userId=900000102");
  });

  it("ignores a next it cannot honour rather than following it", () => {
    expect(frontDoorHref({ next: "https://evil.example", userId: "900000102" })).toBe(
      "/discover?userId=900000102",
    );
    expect(frontDoorHref({ next: "" })).toBe("/discover?userId=900000101");
  });
});
