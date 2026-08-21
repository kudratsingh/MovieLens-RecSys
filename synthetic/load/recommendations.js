import { check, sleep } from "k6";
import exec from "k6/execution";
import http from "k6/http";
import { Counter, Trend } from "k6/metrics";

import { authorizationHeaders, mintAccessToken } from "./lib/auth.js";
import { recommendationThresholds } from "./thresholds.js";

const PROFILE = __ENV.LOAD_PROFILE || "smoke";
const BASE_URL = __ENV.BASE_URL || "http://api-load:8000";
const KEYCLOAK_URL = __ENV.KEYCLOAK_URL || "http://keycloak:8080";
const WARM_USERS = [900000101, 900000102, 900000103];
const COLD_USER = 900000104;
const MIXED_USERS = [...WARM_USERS, COLD_USER];
const LEARNED_POLICY = "item-item-cosine+lightgbm";
const POPULARITY_POLICY = "popularity";

// Mirrors the `--workers` value on the api-load service in
// docker-compose.demo.yml. Every worker is a separate process holding its own
// JWKS cache, database pool, and sidecar connection pool, and every
// model-server worker holds its own per-(user, candidate-set) feature cache.
// A warm-up that reaches one worker leaves the others to pay their first-hit
// cost inside the measured window, which is exactly where the old tail came
// from: cold Feast lookups in the first two seconds of the scenario.
const API_WORKERS = Number(__ENV.API_WORKERS || 4);
const WARMUP_PER_PERSONA = Math.max(2 * API_WORKERS, 2);
const WARMUP_MIN_ROUNDS = 2;
const WARMUP_MAX_ROUNDS = Number(__ENV.WARMUP_MAX_ROUNDS || 3);
// k6 divides request counts by the *whole* test-run duration, and setup() is
// part of it. Warm-up time is therefore spent out of the achieved-throughput
// threshold's margin, so it is bounded here and reported in the summary.
const WARMUP_BUDGET_MS = Number(__ENV.WARMUP_BUDGET_MS || 3000);
// A round is "stable" once its slowest response looks like a warm one. This
// is a warm-up exit condition, not an SLO — the SLO is the p99 threshold.
const WARMUP_STABLE_MS = Number(__ENV.WARMUP_STABLE_MS || 60);
const READINESS_ATTEMPTS = Number(__ENV.READINESS_ATTEMPTS || 10);
// Where handleSummary writes the machine-readable summary. The wrapper reads
// it back to decide whether a breached window earned a re-measurement, so the
// evidence has to leave the container as a file, not only as log output.
const RESULTS_DIR = __ENV.RESULTS_DIR || "/results";

const warmupRequests = new Counter("warmup_requests");
const warmupRounds = new Counter("warmup_rounds");
const warmupDuration = new Trend("warmup_duration_ms");
// A warm persona served by the popularity fallback still answers HTTP 200.
// Counting it gives the failure a name in Prometheus instead of leaving it as
// an unexplained check-rate dip.
const silentFallbacks = new Counter("silent_learned_fallbacks");

const profiles = {
  smoke: {
    duration: "60s",
    // maxVUs is deliberately well above preAllocatedVUs. At the ceiling the
    // executor drops arrivals it cannot start, which silently removes the
    // slowest requests from the measured percentiles and depresses the
    // achieved rate. Headroom makes the measurement stricter, not looser.
    preAllocatedVUs: 10,
    maxVUs: 40,
    rate: 55,
  },
  nightly: {
    duration: "5m",
    preAllocatedVUs: 100,
    maxVUs: 400,
    rate: 600,
  },
};

if (!(PROFILE in profiles)) {
  throw new Error(`Unknown LOAD_PROFILE ${PROFILE}`);
}

const selected = profiles[PROFILE];

export const options = {
  batchPerHost: 16,
  discardResponseBodies: false,
  scenarios: {
    recommendations: {
      executor: "constant-arrival-rate",
      exec: "recommendationTraffic",
      rate: selected.rate,
      timeUnit: "1s",
      preAllocatedVUs: selected.preAllocatedVUs,
      maxVUs: selected.maxVUs,
      duration: selected.duration,
    },
  },
  thresholds: recommendationThresholds,
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(95)", "p(99)", "max"],
};

export function setup() {
  const config = {
    keycloakUrl: KEYCLOAK_URL,
    realm: __ENV.KEYCLOAK_REALM || "demo",
    clientId: __ENV.KEYCLOAK_CLIENT_ID || "movielens-api",
    clientSecret:
      __ENV.KEYCLOAK_CLIENT_SECRET || "movielens-api-secret-dev-only",
    username: __ENV.KEYCLOAK_USERNAME || "demo",
    password: __ENV.KEYCLOAK_PASSWORD || "demo",
  };
  waitForReadiness();
  const auth = { config, ...mintAccessToken(config) };
  warmUpWorkers(auth);
  return { auth };
}

function waitForReadiness() {
  for (let attempt = 1; attempt <= READINESS_ATTEMPTS; attempt++) {
    const response = http.get(`${BASE_URL}/healthz`, {
      tags: { endpoint: "readiness" },
      timeout: "2s",
    });
    if (response.status === 200) {
      return;
    }
    sleep(0.5);
  }
  throw new Error(
    `${BASE_URL}/healthz never answered 200 across ${READINESS_ATTEMPTS} attempts. ` +
      "The load stack is not up: check api-load, model-server, and feature-server.",
  );
}

/**
 * Drive real authenticated traffic through every worker before measurement.
 *
 * Priming through the actual endpoint is what matters: the sidecar's feature
 * cache is keyed by (tenant, user, candidate set), so warming a process
 * without warming that key pays nothing. Each persona therefore gets at least
 * two requests per worker, repeated until a round looks warm.
 */
function warmUpWorkers(auth) {
  const startedAt = Date.now();
  let rounds = 0;
  let requests = 0;
  let slowestMs = 0;
  for (let round = 0; round < WARMUP_MAX_ROUNDS; round++) {
    slowestMs = warmUpRound(auth, round);
    rounds += 1;
    requests += WARMUP_PER_PERSONA * MIXED_USERS.length;
    if (rounds < WARMUP_MIN_ROUNDS) {
      continue;
    }
    if (slowestMs <= WARMUP_STABLE_MS) {
      break;
    }
    const elapsedMs = Date.now() - startedAt;
    if (elapsedMs >= WARMUP_BUDGET_MS) {
      console.warn(
        `warm-up stopped after ${rounds} rounds: ${elapsedMs} ms spent against a ` +
          `${WARMUP_BUDGET_MS} ms budget, slowest response still ${slowestMs.toFixed(1)} ms`,
      );
      break;
    }
  }
  const durationMs = Date.now() - startedAt;
  warmupRounds.add(rounds);
  warmupRequests.add(requests);
  warmupDuration.add(durationMs);
  console.log(
    `warm-up complete: rounds=${rounds} requests=${requests} ` +
      `workers=${API_WORKERS} slowest_ms=${slowestMs.toFixed(1)} duration_ms=${durationMs}`,
  );
}

function warmUpRound(auth, round) {
  const headers = authorizationHeaders(auth);
  const userIds = [];
  for (let slot = 0; slot < WARMUP_PER_PERSONA; slot++) {
    for (let index = 0; index < MIXED_USERS.length; index++) {
      // Rotate which persona leads each round. k6 reuses the batch's
      // connections and a connection stays pinned to one uvicorn worker, so a
      // fixed order would keep re-priming the same (worker, persona) pairs.
      userIds.push(MIXED_USERS[(index + round) % MIXED_USERS.length]);
    }
  }
  const responses = http.batch(
    userIds.map((userId) => ({
      method: "GET",
      url: recommendationUrl(userId),
      params: {
        headers,
        tags: { endpoint: "warmup" },
        // Generous on purpose: a first-hit Feast lookup on a busy host is slow
        // but not broken, and a clear warm-up error beats a mystery threshold
        // breach in the measured window.
        timeout: "10s",
      },
    })),
  );
  // The first round is the one paying the cold cost, so it is allowed to
  // observe the fallback it is there to prevent: a cold sidecar call can
  // exceed the API's model-server timeout and answer HTTP 200 with the
  // popularity policy. Every later round is a readiness assertion — if the
  // stack is still degrading once warm, the run must stop rather than report
  // a fast, wrong p99.
  const enforcePolicy = round > 0;
  let slowestMs = 0;
  let degraded = 0;
  for (const [index, response] of responses.entries()) {
    const userId = userIds[index];
    if (!assertWarmUpResponse(response, userId, round, enforcePolicy)) {
      degraded += 1;
    }
    slowestMs = Math.max(slowestMs, response.timings.duration);
  }
  if (degraded > 0) {
    console.warn(
      `warm-up round ${round + 1}: ${degraded}/${userIds.length} responses came back ` +
        "on the popularity fallback while the caches were still cold",
    );
  }
  return slowestMs;
}

function assertWarmUpResponse(response, userId, round, enforcePolicy) {
  const stage = `warm-up round ${round + 1}`;
  const expected = expectedPolicy(userId);
  const learnedExpected = expected !== POPULARITY_POLICY;
  const policy = response.status === 200 ? response.json("serving_policy") : null;
  if (!policy || typeof policy.name !== "string") {
    throw new Error(
      `${stage} failed for user ${userId}: HTTP ${response.status} with no serving_policy. ` +
        `Body: ${response.body}. The load stack is not serving the seeded demo personas — ` +
        "check `make demo-seed`, model-server, and feature-server before trusting any latency number.",
    );
  }
  const matched =
    policy.name === expected &&
    policy.learned === learnedExpected &&
    response.json("policy") === expected;
  if (matched) {
    return true;
  }
  if (!enforcePolicy) {
    return false;
  }
  throw new Error(
    `${stage} failed for user ${userId}: HTTP ${response.status}, ` +
      `policy ${policy.name} (learned=${policy.learned}), expected ${expected}. ` +
      `Reason: ${policy.reason}. The stack is still degrading after priming — a ` +
      "`model-server-unavailable` reason here means the sidecar call is exceeding " +
      "model_server_timeout_seconds even warm, and the run would have measured a fast wrong answer.",
  );
}

function expectedPolicy(userId) {
  return userId === COLD_USER ? POPULARITY_POLICY : LEARNED_POLICY;
}

function recommendationUrl(userId) {
  return `${BASE_URL}/users/${userId}/recommendations?limit=8`;
}

export function recommendationTraffic(data) {
  routeTraffic(data);
}

function routeTraffic(data) {
  const iteration = exec.scenario.iterationInTest;
  const bucket = iteration % 12;
  if (bucket < 7) {
    const userId = WARM_USERS[iteration % WARM_USERS.length];
    recommend(data.auth, userId, LEARNED_POLICY, "warm");
    return;
  }
  if (bucket < 9) {
    recommend(data.auth, COLD_USER, POPULARITY_POLICY, "cold");
    return;
  }

  const mixedIteration = Math.floor(iteration / 12) * 3 + (bucket - 9);
  const userId = MIXED_USERS[mixedIteration % MIXED_USERS.length];
  recommend(data.auth, userId, expectedPolicy(userId), "mixed");
}

// Bound the per-VU log volume: a fully degraded run would otherwise emit one
// line per request and bury the summary.
const SILENT_FALLBACK_LOG_LIMIT = 3;
let silentFallbacksLogged = 0;

function recommend(auth, userId, expected, traffic) {
  const learnedExpected = expected !== POPULARITY_POLICY;
  const response = http.get(recommendationUrl(userId), {
    headers: authorizationHeaders(auth),
    tags: { endpoint: "recommendations", traffic },
    timeout: "2s",
  });
  const policy = response.status === 200 ? response.json("serving_policy") : null;
  if (learnedExpected && !(policy && policy.learned === true)) {
    // A warm persona degraded to the popularity fallback answers HTTP 200 with
    // a well-formed body. Left unnamed it would only show up as a check-rate
    // dip, so it gets its own metric and a bounded log line carrying the
    // service's own reason (model-server timeout, empty result, ...).
    silentFallbacks.add(1, { traffic });
    if (silentFallbacksLogged < SILENT_FALLBACK_LOG_LIMIT) {
      silentFallbacksLogged += 1;
      console.warn(
        `silent learned fallback: user=${userId} traffic=${traffic} ` +
          `status=${response.status} policy=${policy ? policy.name : "none"} ` +
          `reason=${policy ? policy.reason : "no serving_policy in body"}`,
      );
    }
  }
  check(
    response,
    {
      "HTTP 200": (value) => value.status === 200,
      "expected serving policy": (value) => value.json("policy") === expected,
      // Non-negotiable #4 is a latency SLO on *correct* serving. A warm
      // persona that quietly degraded to popularity is fast and wrong, so the
      // learned claim is asserted rather than inferred from the status code.
      "warm traffic served by the learned path": (value) => {
        if (!learnedExpected) {
          return true;
        }
        const servingPolicy = value.json("serving_policy");
        return !!servingPolicy && servingPolicy.learned === true;
      },
      // The policy object is what the UI uses to claim fallback or learned
      // serving, so the load gate holds it to the same truth as the flat field.
      "serving policy object agrees with the flat policy": (value) => {
        const servingPolicy = value.json("serving_policy");
        return (
          !!servingPolicy &&
          servingPolicy.name === expected &&
          servingPolicy.threshold === 5 &&
          servingPolicy.learned === learnedExpected &&
          typeof servingPolicy.score_scale === "string" &&
          servingPolicy.score_scale.length > 0
        );
      },
      "non-empty recommendation list": (value) => {
        const items = value.json("items");
        return Array.isArray(items) && items.length > 0;
      },
      "auditable request id": (value) =>
        typeof value.headers["X-Request-Id"] === "string",
    },
    { endpoint: "recommendations", traffic },
  );
}

export function handleSummary(data) {
  const duration = metricValues(
    data,
    "http_req_duration{endpoint:recommendations}",
  );
  const requests = metricValues(data, "http_reqs{endpoint:recommendations}");
  const failures = metricValues(
    data,
    "http_req_failed{endpoint:recommendations}",
  );
  const dropped = metricValues(data, "dropped_iterations").count || 0;
  const silent = metricValues(data, "silent_learned_fallbacks").count || 0;
  const summary = {
    profile: PROFILE,
    workload: {
      duration: selected.duration,
      preallocated_vus: selected.preAllocatedVUs,
      max_vus: selected.maxVUs,
      target_rps: selected.rate,
      traffic: ["warm", "cold", "mixed"],
    },
    warmup: {
      api_workers: API_WORKERS,
      rounds: metricValues(data, "warmup_rounds").count || 0,
      requests: metricValues(data, "warmup_requests").count || 0,
      duration_ms: metricValues(data, "warmup_duration_ms").max || 0,
    },
    latency_ms: {
      p50: duration["p(50)"],
      p95: duration["p(95)"],
      p99: duration["p(99)"],
    },
    throughput_rps: requests.rate,
    request_count: requests.count,
    // k6 divides the request count by this to get throughput_rps, and it
    // includes setup(). Reported so warm-up cost against the achieved-rate
    // threshold is visible rather than inferred.
    test_run_duration_s: data.state ? data.state.testRunDurationMs / 1000 : null,
    error_rate: failures.rate,
    check_rate: metricValues(data, "checks{endpoint:recommendations}").rate,
    dropped_iterations: dropped,
    silent_learned_fallbacks: silent,
  };
  const notes = [];
  if (dropped > 0) {
    notes.push(
      `WARNING: ${dropped} iterations were never started, so the slowest requests ` +
        "are missing from these percentiles and the achieved rate is understated. " +
        "Raise maxVUs or treat the run as capacity-limited rather than SLO-passing.",
    );
  }
  if (silent > 0) {
    notes.push(
      `WARNING: ${silent} warm responses degraded to a non-learned policy while ` +
        "returning HTTP 200. The check rate above already fails; inspect model-server.",
    );
  }
  const prefix = notes.length > 0 ? `${notes.join("\n")}\n` : "";
  const rendered = `${JSON.stringify(summary, null, 2)}\n`;
  const output = { stdout: `${prefix}${rendered}` };
  if (RESULTS_DIR) {
    output[`${RESULTS_DIR}/summary.json`] = rendered;
  }
  return output;
}

function metricValues(data, name) {
  const metric = data.metrics[name];
  return metric && metric.values ? metric.values : {};
}
