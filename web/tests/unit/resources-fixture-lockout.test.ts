import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FIXTURE_REQUEST_ID,
  FixtureModeUnavailableError,
  fixtureResourcesEnabled,
  fixtureResourceState,
  injectedResourceFailure,
} from "@/lib/resources/fixture-gate";
import { loadRecommendations, type FetchLike } from "@/lib/resources/server";

import { recommendationResponse } from "./resource-fixtures";

const PRODUCTION = { NODE_ENV: "production", MOVIELENS_UI_FIXTURE_MODE: "1" };
const FIXTURE_MODE = { NODE_ENV: "test", MOVIELENS_UI_FIXTURE_MODE: "1" };
const NORMAL_DEV = { NODE_ENV: "development", MOVIELENS_UI_FIXTURE_MODE: undefined };

function moduleSource(relativePath: string) {
  // Vitest runs with the web package as its root.
  return readFileSync(resolve(process.cwd(), relativePath), "utf8");
}

afterEach(() => vi.unstubAllEnvs());

describe("recorded fixtures are test inputs, never a fallback", () => {
  it("locks fixtures out of a production build even with the flag set", () => {
    expect(fixtureResourcesEnabled(PRODUCTION)).toBe(false);
    expect(() =>
      fixtureResourceState("recommendations", recommendationResponse, {
        environment: PRODUCTION,
      }),
    ).toThrowError(FixtureModeUnavailableError);
    expect(() =>
      injectedResourceFailure(
        "recommendations",
        { status: "upstream-error", reason: "server" },
        PRODUCTION,
      ),
    ).toThrowError(FixtureModeUnavailableError);
  });

  it("locks fixtures out of a normal development run without the explicit flag", () => {
    expect(fixtureResourcesEnabled(NORMAL_DEV)).toBe(false);
    expect(() =>
      fixtureResourceState("catalog", recommendationResponse, { environment: NORMAL_DEV }),
    ).toThrowError(FixtureModeUnavailableError);
  });

  it("tags anything it does hand out so the source is visible and assertable", () => {
    const state = fixtureResourceState("recommendations", recommendationResponse, {
      environment: FIXTURE_MODE,
    });
    const injected = injectedResourceFailure(
      "recommendations",
      { status: "upstream-error", reason: "timeout" },
      FIXTURE_MODE,
    );

    expect(state.status === "ready" && state.source).toBe("recorded-contract-fixture");
    expect(state.status === "ready" && state.requestId).toBe(FIXTURE_REQUEST_ID);
    expect(injected.reason).toBe("timeout");
    expect(injected.retryable).toBe(true);
  });

  it("returns a visible failure rather than recorded data when a live read fails", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("MOVIELENS_UI_FIXTURE_MODE", "1");
    const offline: FetchLike = () => Promise.reject(new TypeError("fetch failed"));

    const state = await loadRecommendations(900000101, {
      session: { accessToken: "token" },
      fetchImpl: offline,
    });

    expect(state.status).toBe("upstream-error");
    expect(state).not.toHaveProperty("data");
    expect(JSON.stringify(state)).not.toContain("recorded-contract-fixture");
  });

  it("keeps the live client structurally unable to reach a fixture", () => {
    // A convention would drift. Reading the module keeps the guarantee honest:
    // if a future edit imports recorded data into the live client, this fails.
    const source = moduleSource("lib/resources/server.ts");

    expect(source).toContain('import "server-only"');
    expect(source).not.toMatch(/from\s+"@\/lib\/fixtures/);
    expect(source).not.toMatch(/from\s+"@\/lib\/resources\/fixture-gate"/);
    expect(source).not.toMatch(/MOVIELENS_UI_FIXTURE_MODE/);
  });
});
