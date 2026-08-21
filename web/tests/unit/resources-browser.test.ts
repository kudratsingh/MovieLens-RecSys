import { describe, expect, it } from "vitest";

import { LIBRARY, RECOMMENDATIONS } from "@/lib/resources/definitions";
import {
  ForwardedCredentialError,
  readBffResource,
} from "@/lib/resources/browser";
import { isResourceFailure } from "@/lib/resources/state";

import { libraryResponse, recommendationResponse } from "./resource-fixtures";

type Recorded = { url: string; init: RequestInit | undefined };

function recorder(response: () => Response) {
  const calls: Recorded[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return response();
  }) as typeof fetch;
  return { calls, fetchImpl };
}

describe("the browser reader", () => {
  it("refuses to forward a caller-supplied bearer token", async () => {
    const { calls, fetchImpl } = recorder(() => Response.json(libraryResponse));

    await expect(
      readBffResource(LIBRARY, "/api/users/900000101/library", {
        fetchImpl,
        headers: { Authorization: "Bearer stolen-token" },
      }),
    ).rejects.toBeInstanceOf(ForwardedCredentialError);
    await expect(
      readBffResource(LIBRARY, "/api/users/900000101/library", {
        fetchImpl,
        headers: { "Proxy-Authorization": "Basic abc" },
      }),
    ).rejects.toBeInstanceOf(ForwardedCredentialError);
    expect(calls).toHaveLength(0);
  });

  it("sends a same-origin, no-store request carrying only a correlation ID", async () => {
    const { calls, fetchImpl } = recorder(() => Response.json(libraryResponse));

    const state = await readBffResource(LIBRARY, "/api/users/900000101/library", {
      fetchImpl,
    });

    expect(state.status).toBe("ready");
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.has("authorization")).toBe(false);
    expect(headers.get("accept")).toBe("application/json");
    expect(headers.get("x-request-id")).toBeTruthy();
    expect(calls[0].init?.credentials).toBe("same-origin");
    expect(calls[0].init?.cache).toBe("no-store");
  });

  it("maps BFF outcomes to the same states the server client produces", async () => {
    const expired = await readBffResource(RECOMMENDATIONS, "/api/x", {
      fetchImpl: recorder(() => Response.json({ detail: "expired" }, { status: 401 })).fetchImpl,
    });
    const forbidden = await readBffResource(RECOMMENDATIONS, "/api/x", {
      fetchImpl: recorder(() => Response.json({ detail: "no" }, { status: 403 })).fetchImpl,
    });
    const gateway = await readBffResource(RECOMMENDATIONS, "/api/x", {
      fetchImpl: recorder(() => Response.json({ detail: "down" }, { status: 502 })).fetchImpl,
    });
    const ready = await readBffResource(RECOMMENDATIONS, "/api/x", {
      fetchImpl: recorder(() => Response.json(recommendationResponse)).fetchImpl,
    });

    expect(expired.status).toBe("auth-expired");
    expect(forbidden.status).toBe("forbidden");
    expect(gateway.status).toBe("upstream-error");
    expect(isResourceFailure(gateway) && gateway.retryable).toBe(true);
    expect(ready.status).toBe("ready");
  });

  it("reports a timed-out region without disturbing anything else on the page", async () => {
    const state = await readBffResource(RECOMMENDATIONS, "/api/x", {
      fetchImpl: (() =>
        Promise.reject(new DOMException("aborted", "TimeoutError"))) as typeof fetch,
    });

    expect(state.resource).toBe("recommendations");
    expect(isResourceFailure(state) && state.reason).toBe("timeout");
  });
});
