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

  it("leaves other routes to their own caching rules", () => {
    const response = middleware(request("https://movielens.test/browse"));

    expect(response.headers.get("Cache-Control")).toBeNull();
  });

  it("runs only where it is needed", () => {
    expect(config.matcher).toEqual(["/discover"]);
  });
});
