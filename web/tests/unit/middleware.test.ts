import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { config, isPersonalizedDocument, middleware } from "@/middleware";

function request(url: string) {
  return new NextRequest(new Request(url));
}

describe("personalized documents are not shared-cacheable", () => {
  it("marks the Discover document private and uncacheable", () => {
    const response = middleware(request("https://movielens.test/discover?userId=900000101"));

    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("marks the front door private, because its answer depends on the session", () => {
    // Signed out it is the sign-in door; signed in it is a redirect carrying a
    // persona. A shared cache holding either one serves it to the wrong
    // visitor, which is the failure the cutover introduced the risk of.
    const response = middleware(request("https://movielens.test/"));

    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("marks the retained legacy dashboard private", () => {
    const response = middleware(request("https://movielens.test/legacy"));

    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("covers the three routes the original list left off", () => {
    // Library, movie detail, and Quick Picks each render a named persona's
    // saved state into the HTML, which is the list's own stated criterion.
    for (const path of [
      "/library?userId=900000102&tab=watchlist",
      "/movies/296?user=900000103",
      "/quick-picks?user=900000104",
    ]) {
      const response = middleware(request(`https://movielens.test${path}`));

      expect(response.headers.get("Cache-Control"), path).toBe("private, no-store");
    }
  });

  it("matches movie detail by prefix rather than by exact path", () => {
    expect(isPersonalizedDocument("/movies/1")).toBe(true);
    expect(isPersonalizedDocument("/movies/296")).toBe(true);
    // Nothing serves this, and matching it would be an accident either way.
    expect(isPersonalizedDocument("/movies")).toBe(false);
  });

  it("leaves other routes to their own caching rules", () => {
    const response = middleware(request("https://movielens.test/browse"));

    expect(response.headers.get("Cache-Control")).toBeNull();
  });

  it("runs everywhere it is needed and nowhere else", () => {
    // The matcher and the predicate have to agree: a path the predicate marks
    // but the matcher never routes here is a header that silently never ships.
    expect(config.matcher).toEqual([
      "/",
      "/discover",
      "/legacy",
      "/library",
      "/quick-picks",
      "/movies/:path*",
    ]);
    for (const path of config.matcher) {
      const sample = path.replace("/:path*", "/296");
      expect(isPersonalizedDocument(sample), sample).toBe(true);
    }
  });
});
