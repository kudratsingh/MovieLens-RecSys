import { describe, expect, it } from "vitest";

import { rejectForwardedCredentials } from "@/lib/bff-security";
import { RECOMMENDATIONS } from "@/lib/resources/definitions";
import {
  resourceRequestId,
  resourceResponse,
  resourceRouteResponse,
} from "@/lib/resources/bff";
import {
  emptyState,
  failureState,
  loadingState,
  readyState,
  type ResourceState,
} from "@/lib/resources/state";

import { emptyRecommendationResponse, recommendationResponse } from "./resource-fixtures";

const REQUEST_ID = "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001";

function bffRequest(headers: HeadersInit = {}) {
  return new Request("http://localhost:3001/api/users/900000101/recommendations", {
    headers,
  });
}

describe("the BFF resource boundary", () => {
  it("refuses a caller-supplied credential rather than ignoring it", async () => {
    expect(() =>
      rejectForwardedCredentials(bffRequest({ authorization: "Bearer stolen" })),
    ).toThrowError(/must not supply their own API credentials/);
    expect(() => rejectForwardedCredentials(bffRequest())).not.toThrow();

    const refused = await resourceRouteResponse(
      bffRequest({ authorization: "Bearer stolen" }),
      async () => readyState("recommendations", recommendationResponse, REQUEST_ID),
    );

    expect(refused.status).toBe(403);
    expect(refused.headers.get("cache-control")).toBe("private, no-store");
  });

  it("adopts a well-formed caller request ID and replaces a malformed one", () => {
    expect(resourceRequestId(bffRequest({ "x-request-id": REQUEST_ID }))).toBe(REQUEST_ID);
    expect(resourceRequestId(bffRequest({ "x-request-id": "nope" }))).not.toBe("nope");
    expect(resourceRequestId(bffRequest())).toBeTruthy();
  });

  it("hands the correlation ID to the loader and echoes it to the browser", async () => {
    const seen: string[] = [];

    const response = await resourceRouteResponse(
      bffRequest({ "x-request-id": REQUEST_ID }),
      async (requestId) => {
        seen.push(requestId);
        return readyState("recommendations", recommendationResponse, requestId);
      },
    );

    expect(seen).toEqual([REQUEST_ID]);
    expect(response.headers.get("x-request-id")).toBe(REQUEST_ID);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    await expect(response.json()).resolves.toMatchObject({ policy: "item-item-lightgbm" });
  });

  it("keeps a personalized empty page a 200 with its page metadata", async () => {
    const response = resourceResponse(
      emptyState("recommendations", emptyRecommendationResponse, REQUEST_ID),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    await expect(response.json()).resolves.toMatchObject({ items: [], tenant_id: "demo" });
  });

  it("translates each failure state into the status the browser expects", async () => {
    const cases = [
      ["auth-expired", "session-expired", 401],
      ["forbidden", "forbidden", 403],
      ["not-found", "not-found", 404],
      ["upstream-error", "timeout", 504],
      ["upstream-error", "rate-limited", 429],
      ["upstream-error", "server", 502],
      ["upstream-error", "invalid-payload", 502],
    ] as const;

    for (const [status, reason, expected] of cases) {
      const response = resourceResponse(
        failureState({ status, resource: "recommendations", reason, requestId: REQUEST_ID }),
      );
      expect([reason, response.status]).toEqual([reason, expected]);
      expect(response.headers.get("cache-control")).toBe("private, no-store");
      await expect(response.json()).resolves.toMatchObject({ reason });
    }
  });

  it("treats an unresolved state as a server defect, not an upstream failure", async () => {
    const response = resourceResponse(
      loadingState(RECOMMENDATIONS.name) as ResourceState<never>,
    );

    expect(response.status).toBe(500);
  });
});
