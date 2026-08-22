import { expect, test } from "@playwright/test";

import { signInThroughKeycloak } from "./keycloak";
import { COLD_START, readColdStartContract } from "./personas";

/**
 * The post-suite persona guard.
 *
 * Every journey in the run promises to leave the persona it borrowed the way it
 * found it, and Cold Start's version of that promise is the strict one: zero
 * positive signals, because the whole run's cold-start assertions are about a
 * persona with nothing to learn from. Nothing in the journeys reads a count
 * that low, though — they assert `< 5`, or `before + 1` against whatever they
 * found — so a journey that leaks a single watched signal passes every test in
 * this directory and then fails something else entirely. That is exactly what
 * happened: the PKCE journey's "reset" was a bulk *rating* clear, which
 * preserves the watched row, and the leak surfaced in the k6 page workload's
 * teardown guard on a stack the browser suites had already declared clean.
 *
 * This runs last, on purpose, and is deliberately cheap: one sign-in and two
 * reads. It repairs nothing — a persona that needed repairing here is a journey
 * that forgot its own cleanup, and the point is that the run says so.
 *
 * It is the last file in `test:e2e`'s list and sorts last alphabetically, so it
 * stays last whether the suite is run through the script or through a bare
 * `playwright test`.
 */
test("the run leaves Cold Start with zero positive signals", async ({ page }) => {
  await signInThroughKeycloak(page);

  const contract = await readColdStartContract(page);
  const evidence =
    `policy ${contract.policy}, ${contract.positiveSignals} positive signals, ` +
    `watched ${JSON.stringify(contract.watched)}`;

  // The model input first: this is the value serving routes on, and the one the
  // k6 workload's own teardown reads back.
  expect(contract.positiveSignals, `Cold Start was left dirty — ${evidence}`).toBe(0);
  expect(contract.learned, `Cold Start reported learned serving — ${evidence}`).toBe(false);
  // And the history behind it, which names the title a journey forgot rather
  // than only reporting that the count moved.
  expect(contract.watched, `a journey left watched titles on ${COLD_START}`).toEqual([]);
  expect(contract.historyCount, `a journey left watched titles on ${COLD_START}`).toBe(0);
});
