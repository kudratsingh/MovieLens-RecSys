/**
 * The only door recorded fixtures may come through.
 *
 * Fixtures are test inputs: component tests, the screenshot harness, and
 * deliberate failure injection. They are not a fallback. The lockout is
 * structural rather than conventional in two ways:
 *
 * 1. `lib/resources/server.ts` — the module that actually talks to FastAPI —
 *    does not import this file or any fixture, so no live read can reach one;
 *    `resources-fixture-lockout.test.ts` asserts that stays true.
 * 2. Asking for a fixture outside the explicit fixture mode throws instead of
 *    returning data, so a mistaken call fails loudly at the point of the
 *    mistake rather than shipping recorded movies to a real viewer.
 *
 * Anything a fixture produces is tagged `recorded-contract-fixture`, so the
 * source is legible in the UI and assertable in a test.
 */

import { isolatedUiPreviewMode } from "@/lib/ui-preview-access";
import {
  emptyState,
  failureState,
  readyState,
  type LiveResourceName,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";

/** Stable placeholder so recorded evidence panels have something to render. */
export const FIXTURE_REQUEST_ID = "fixture-00000000-0000-0000-0000-000000000000";

export class FixtureModeUnavailableError extends Error {
  constructor(resource: LiveResourceName) {
    super(
      `Recorded fixtures for "${resource}" are unavailable: fixture mode is off. Production reads must surface a real failure instead.`,
    );
    this.name = "FixtureModeUnavailableError";
  }
}

type FixtureEnvironment = {
  NODE_ENV?: string;
  MOVIELENS_UI_FIXTURE_MODE?: string;
};

export function fixtureResourcesEnabled(
  environment: FixtureEnvironment = process.env,
): boolean {
  return isolatedUiPreviewMode(environment);
}

function requireFixtureMode(
  resource: LiveResourceName,
  environment: FixtureEnvironment,
) {
  if (!fixtureResourcesEnabled(environment)) {
    throw new FixtureModeUnavailableError(resource);
  }
}

export function fixtureResourceState<T>(
  resource: LiveResourceName,
  data: T,
  options: { empty?: boolean; environment?: FixtureEnvironment } = {},
): ResourceState<T> {
  const environment = options.environment ?? process.env;
  requireFixtureMode(resource, environment);
  return options.empty
    ? emptyState(resource, data, FIXTURE_REQUEST_ID, "recorded-contract-fixture")
    : readyState(resource, data, FIXTURE_REQUEST_ID, "recorded-contract-fixture");
}

/** Deliberate failure injection for screenshots and partial-failure tests. */
export function injectedResourceFailure(
  resource: LiveResourceName,
  failure: Pick<ResourceFailure, "status" | "reason">,
  environment: FixtureEnvironment = process.env,
): ResourceFailure {
  requireFixtureMode(resource, environment);
  return failureState({
    status: failure.status,
    resource,
    reason: failure.reason,
    requestId: FIXTURE_REQUEST_ID,
  });
}
