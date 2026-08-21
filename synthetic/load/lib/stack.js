import http from "k6/http";
import { sleep } from "k6";

/**
 * Block until the API answers `/healthz`, or fail the run with a useful reason.
 *
 * `recommendations.js` deliberately keeps its own copy of this. That script is
 * the pinned p99 gate (non-negotiables #4/#11); its behaviour is the contract
 * every recorded baseline was measured against, so this bundle adds to the
 * harness without editing it. New scripts import from here.
 */
export function waitForReadiness(baseUrl, attempts) {
  const total = attempts || 10;
  for (let attempt = 1; attempt <= total; attempt++) {
    const response = http.get(`${baseUrl}/healthz`, {
      tags: { endpoint: "readiness" },
      timeout: "2s",
    });
    if (response.status === 200) {
      return;
    }
    sleep(0.5);
  }
  throw new Error(
    `${baseUrl}/healthz never answered 200 across ${total} attempts. ` +
      "The load stack is not up: check api-load, model-server, and feature-server.",
  );
}

/**
 * A UUID-v4-shaped string, built without importing a crypto module.
 *
 * The API requires `Idempotency-Key` to parse as a UUID and rejects anything
 * else with a 422, so the shape matters. Uniqueness here only has to hold
 * within one run; this is a request-deduplication token, not a secret.
 */
export function uuid4() {
  const digits = "0123456789abcdef";
  let value = "";
  for (let position = 0; position < 36; position++) {
    if (position === 8 || position === 13 || position === 18 || position === 23) {
      value += "-";
    } else if (position === 14) {
      value += "4";
    } else if (position === 19) {
      value += digits[8 + Math.floor(Math.random() * 4)];
    } else {
      value += digits[Math.floor(Math.random() * 16)];
    }
  }
  return value;
}
