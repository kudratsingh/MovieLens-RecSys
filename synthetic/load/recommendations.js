import { check } from "k6";
import exec from "k6/execution";
import http from "k6/http";

import { authorizationHeaders, mintAccessToken } from "./lib/auth.js";
import { recommendationThresholds } from "./thresholds.js";

const PROFILE = __ENV.LOAD_PROFILE || "smoke";
const BASE_URL = __ENV.BASE_URL || "http://api-load:8000";
const KEYCLOAK_URL = __ENV.KEYCLOAK_URL || "http://keycloak:8080";
const WARM_USERS = [900000101, 900000102, 900000103];
const COLD_USER = 900000104;
const MIXED_USERS = [...WARM_USERS, COLD_USER];

const profiles = {
  smoke: {
    duration: "60s",
    virtualUsers: 10,
    rate: 55,
  },
  nightly: {
    duration: "5m",
    virtualUsers: 100,
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
      preAllocatedVUs: selected.virtualUsers,
      maxVUs: selected.virtualUsers,
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
  const auth = { config, ...mintAccessToken(config) };

  const userIds = Array.from({ length: 4 }, () => MIXED_USERS).flat();
  const headers = authorizationHeaders(auth);
  const responses = http.batch(
    userIds.map((userId) => ({
      method: "GET",
      url: `${BASE_URL}/users/${userId}/recommendations?limit=8`,
      params: {
        headers,
        tags: { endpoint: "setup" },
        timeout: "2s",
      },
    })),
  );
  for (const [index, response] of responses.entries()) {
    const userId = userIds[index];
    const expectedPolicy = userId === COLD_USER ? "popularity" : "item-item-cosine+lightgbm";
    if (response.status !== 200 || response.json("policy") !== expectedPolicy) {
      throw new Error(
        `Setup failed for user ${userId}: HTTP ${response.status}, policy ${response.json("policy")}`,
      );
    }
  }
  return { auth };
}

export function recommendationTraffic(data) {
  routeTraffic(data);
}

function routeTraffic(data) {
  const iteration = exec.scenario.iterationInTest;
  const bucket = iteration % 12;
  if (bucket < 7) {
    const userId = WARM_USERS[iteration % WARM_USERS.length];
    recommend(data.auth, userId, "item-item-cosine+lightgbm", "warm");
    return;
  }
  if (bucket < 9) {
    recommend(data.auth, COLD_USER, "popularity", "cold");
    return;
  }

  const mixedIteration = Math.floor(iteration / 12) * 3 + (bucket - 9);
  const userId = MIXED_USERS[mixedIteration % MIXED_USERS.length];
  const expectedPolicy =
    userId === COLD_USER ? "popularity" : "item-item-cosine+lightgbm";
  recommend(data.auth, userId, expectedPolicy, "mixed");
}

function recommend(auth, userId, expectedPolicy, traffic) {
  const response = http.get(
    `${BASE_URL}/users/${userId}/recommendations?limit=8`,
    {
      headers: authorizationHeaders(auth),
      tags: { endpoint: "recommendations", traffic },
      timeout: "2s",
    },
  );
  check(
    response,
    {
      "HTTP 200": (value) => value.status === 200,
      "expected serving policy": (value) => value.json("policy") === expectedPolicy,
      // The policy object is what the UI uses to claim fallback or learned
      // serving, so the load gate holds it to the same truth as the flat field.
      "serving policy object agrees with the flat policy": (value) => {
        const policy = value.json("serving_policy");
        return (
          !!policy &&
          policy.name === expectedPolicy &&
          policy.threshold === 5 &&
          policy.learned === (expectedPolicy !== "popularity") &&
          typeof policy.score_scale === "string" &&
          policy.score_scale.length > 0
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
  const summary = {
    profile: PROFILE,
    workload: {
      duration: selected.duration,
      virtual_users: selected.virtualUsers,
      target_rps: selected.rate,
      traffic: ["warm", "cold", "mixed"],
    },
    latency_ms: {
      p50: duration["p(50)"],
      p95: duration["p(95)"],
      p99: duration["p(99)"],
    },
    throughput_rps: requests.rate,
    request_count: requests.count,
    error_rate: failures.rate,
    check_rate: metricValues(
      data,
      "checks{endpoint:recommendations}",
    ).rate,
    dropped_iterations: metricValues(data, "dropped_iterations").count || 0,
  };
  return { stdout: `${JSON.stringify(summary, null, 2)}\n` };
}

function metricValues(data, name) {
  const metric = data.metrics[name];
  return metric && metric.values ? metric.values : {};
}
