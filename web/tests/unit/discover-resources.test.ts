import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DISCOVER_SCENARIOS,
  discoverScenario,
  loadDiscoverResources,
} from "@/lib/discover/resources";
import { FixtureModeUnavailableError } from "@/lib/resources/fixture-gate";
import { hasResourceData } from "@/lib/resources/state";

const FIXTURE_MODE = { NODE_ENV: "test", MOVIELENS_UI_FIXTURE_MODE: "1" } as NodeJS.ProcessEnv;
const NORMAL = { NODE_ENV: "development", MOVIELENS_UI_FIXTURE_MODE: undefined } as NodeJS.ProcessEnv;
const PRODUCTION = { NODE_ENV: "production", MOVIELENS_UI_FIXTURE_MODE: "1" } as NodeJS.ProcessEnv;

afterEach(() => vi.unstubAllEnvs());

describe("the recorded Discover harness is opt-in and non-production", () => {
  it("ignores the selector outside the isolated UI mode", () => {
    expect(discoverScenario("fallback", NORMAL)).toBeNull();
    expect(discoverScenario("fallback", PRODUCTION)).toBeNull();
  });

  it("reads live by default even inside the isolated UI mode", () => {
    expect(discoverScenario(undefined, FIXTURE_MODE)).toBeNull();
  });

  it("accepts only the named scenarios", () => {
    for (const scenario of DISCOVER_SCENARIOS) {
      expect(discoverScenario(scenario, FIXTURE_MODE)).toBe(scenario);
    }
    expect(discoverScenario("", FIXTURE_MODE)).toBe("learned");
    expect(discoverScenario("something-else", FIXTURE_MODE)).toBeNull();
  });

  it("throws rather than serving recorded data when the mode is off", async () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("MOVIELENS_UI_FIXTURE_MODE", "1");

    await expect(
      loadDiscoverResources({
        session: { accessToken: "token" },
        userId: 900000101,
        scenario: "learned",
      }),
    ).rejects.toThrowError(FixtureModeUnavailableError);
  });
});

describe("live Discover reads", () => {
  it("returns resource failures instead of recorded data when the session cannot read", async () => {
    const resources = await loadDiscoverResources({
      session: { accessToken: undefined },
      userId: 900000101,
      scenario: null,
    });

    expect(resources.recommendations.status).toBe("auth-expired");
    expect(resources.history.status).toBe("auth-expired");
    expect(resources.recordedEvidence).toBeNull();
    expect(JSON.stringify(resources)).not.toContain("recorded-contract-fixture");
  });

  it("refuses an unaddressable persona without contacting the API", async () => {
    const resources = await loadDiscoverResources({
      session: { accessToken: "token" },
      userId: 0,
      scenario: null,
    });

    expect(resources.recommendations.status).toBe("not-found");
    expect(resources.history.status).toBe("not-found");
  });
});

describe("recorded scenarios cover the states the route has to prove", () => {
  it("keeps one region readable while another fails", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("MOVIELENS_UI_FIXTURE_MODE", "1");

    const historyDown = await loadDiscoverResources({
      session: null,
      userId: 900000101,
      scenario: "history-error",
    });
    expect(historyDown.recommendations.status).toBe("ready");
    expect(historyDown.history.status).toBe("upstream-error");

    const recsDown = await loadDiscoverResources({
      session: null,
      userId: 900000101,
      scenario: "recommendations-error",
    });
    expect(recsDown.recommendations.status).toBe("upstream-error");
    expect(recsDown.history.status).toBe("ready");

    const evidenceDown = await loadDiscoverResources({
      session: null,
      userId: 900000101,
      scenario: "evidence-error",
    });
    expect(evidenceDown.recommendations.status).toBe("ready");
    expect(evidenceDown.recordedEvidence?.audits.status).toBe("upstream-error");
  });

  it("tags every recorded payload as recorded", async () => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubEnv("MOVIELENS_UI_FIXTURE_MODE", "1");

    const resources = await loadDiscoverResources({
      session: null,
      userId: 900000101,
      scenario: "learned",
    });

    expect(
      hasResourceData(resources.recommendations) && resources.recommendations.source,
    ).toBe("recorded-contract-fixture");
  });
});

describe("the live server client stays fixture-free", () => {
  it("keeps the recorded branch out of lib/resources/server.ts", () => {
    // The branch belongs to the route module, not to the client that talks to
    // FastAPI. If that ever inverts, this fails before a reviewer has to spot it.
    const source = readFileSync(resolve(process.cwd(), "lib/resources/server.ts"), "utf8");
    expect(source).not.toMatch(/from\s+"@\/lib\/fixtures/);
    expect(source).not.toMatch(/from\s+"@\/lib\/discover/);
  });
});
