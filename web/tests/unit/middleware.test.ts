import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { config, middleware } from "@/middleware";

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

  it("leaves other routes to their own caching rules", () => {
    const response = middleware(request("https://movielens.test/browse"));

    expect(response.headers.get("Cache-Control")).toBeNull();
  });

  it("runs only where it is needed", () => {
    expect(config.matcher).toEqual(["/", "/discover", "/legacy"]);
  });
});
