// Page-shaped API workloads: what each route of the web client actually asks
// the API for, in the order and the parallelism the route uses.
//
// `recommendations.js` measures one endpoint under a pinned arrival rate and is
// the p99 gate. It cannot see the costs a *page* pays: a fan-out is only as
// fast as its slowest leg, a cursor continuation is three round trips deep, and
// a mutation is not finished when the PUT returns — it is finished when the
// next read shows the committed state. Those are the numbers a reader feels,
// and none of them appear in a single-endpoint percentile.
//
// Each scenario models one route as a sequence of tagged steps, read off the
// loaders in `web/lib/**` rather than guessed:
//
//   discover    recommendations + history + personas, all three concurrent and
//               all three blocking the server render, then the audits/features
//               disclosure that must never block it
//   browse      catalog first page -> next cursor -> next cursor -> open a
//               movie, rotating through the search / genre / decade / sort
//               variants the filter bar can produce
//   library     the active tab + taste profile + personas concurrently, then
//               the two tab switches
//   mutation    read state -> mutate -> replay the same idempotency key ->
//               read state -> the counts refresh and list read the client
//               issues -> revert -> read state
//   quickpicks  recommend -> dismiss -> recommend (id gone) -> undo -> watched
//               -> recommend (policy still coherent) -> revert
//
// Every request carries `page` and `step` tags so a percentile is attributable
// to a route and to a position inside it, and correctness is asserted at every
// step: a fast wrong answer is not a passing measurement.
//
// The `quickpicks` sequence is the API contract the Quick Picks route drives —
// durable suppression, undo, and a coherent policy after a fresh signal —
// rather than a transcript of one page's fetches, because the route's own
// classification happens through server actions rather than a fixed request
// list. It is the shape that has to hold under load either way.
//
// The two writing scenarios follow the same persona ownership table the browser
// suite uses, revert inside the iteration, and are swept again in teardown.
// Cold Start is never mutated, and the run refuses to start if it is pointed
// there: it is handed on at *zero* positive signals, not merely below the
// threshold, and leaving a signal behind would break the browser suite's Quick
// Picks journey, which owns that persona precisely for its signal count.
import { check } from "k6";
import exec from "k6/execution";
import http from "k6/http";
import { Counter, Trend } from "k6/metrics";

import { authorizationHeaders, mintAccessToken } from "./lib/auth.js";
import { uuid4, waitForReadiness } from "./lib/stack.js";
import { PAGE_BUDGETS, STEP_BUDGETS, pageThresholds, thresholdGroup } from "./page_thresholds.js";

const PROFILE = __ENV.LOAD_PROFILE || "smoke";
const BASE_URL = __ENV.BASE_URL || "http://api-load:8000";
const KEYCLOAK_URL = __ENV.KEYCLOAK_URL || "http://keycloak:8080";
const RESULTS_DIR = __ENV.RESULTS_DIR || "/results";
const API_WORKERS = Number(__ENV.API_WORKERS || 4);

const LEARNED_POLICY = "item-item-cosine+lightgbm";
const POPULARITY_POLICY = "popularity";
const FILTER_POLICY = "watched-and-dismissed-excluded-v1";
// ADR 0001 as amended 2026-08-30, mirroring src/evaluation/protocol.py. k6
// cannot import the Python constant, so every check below compares this against
// the `serving_policy.threshold` the response reports rather than asserting the
// number on its own — a drift between the two fails the gate here.
const COLD_START_THRESHOLD = 10;

// Page sizes are the client's, not invented here: `DISCOVER_RECOMMENDATION_LIMIT`
// and `DISCOVER_HISTORY_LIMIT` in web/lib/discover/resources.ts,
// `CATALOG_PAGE_LIMIT` in web/lib/browse/query.ts, `LIBRARY_PAGE_SIZE` in
// web/lib/library/url-state.ts. Measuring a page size the product never asks
// for would produce a budget nothing has to meet.
const DISCOVER_RECOMMENDATION_LIMIT = 10;
const DISCOVER_HISTORY_LIMIT = 8;
const DISCOVER_AUDIT_LIMIT = 5;
const CATALOG_PAGE_SIZE = 24;
// The API's own ceiling on `limit`. Asserted rather than assumed: an unbounded
// page is a latency cliff waiting for a bigger tenant.
const CATALOG_MAX_PAGE_SIZE = 48;
const LIBRARY_PAGE_SIZE = 12;
const LIBRARY_MAX_PAGE_SIZE = 50;

// Persona assignment, mirroring the ownership table the browser suite keeps in
// `web/tests/e2e/browser-auth.spec.ts`: rating and watched history belong to
// Action Fan, watchlist to Eclectic Viewer, Discover's writes to Drama Fan, and
// Cold Start is read-only for everyone. In CI the two suites never share a
// database — the load gate runs under the `movielens-demo` Compose project and
// the browser job under `movielens-browser-e2e` — so a collision is impossible
// there; locally they would share one, which is why the same table governs both
// and why every write here is undone.
//
// Which persona each *reader* uses is a measurement decision on top of that.
// Discover and Browse read Eclectic Viewer because the only writes that persona
// receives are watchlist changes, and watchlist changes no recommendation
// input — so the budgeted recommendation step is coupled to the cheapest
// possible writer. Library reads Action Fan, whose rating edits churn values
// the library reports but not the counts it is asserted on.
//
// Overridable so that a dedicated load persona, if the seeder ever grows one,
// is a variable change rather than an edit to five call sites.
const DISCOVER_WARM_USER = Number(__ENV.LOAD_DISCOVER_USER || 900000103);
const DISCOVER_COLD_USER = Number(__ENV.LOAD_COLD_USER || 900000104);
const BROWSE_USER = Number(__ENV.LOAD_BROWSE_USER || 900000103);
const LIBRARY_USER = Number(__ENV.LOAD_LIBRARY_USER || 900000101);
const RATING_USER = Number(__ENV.LOAD_RATING_USER || 900000101);
const WATCHLIST_USER = Number(__ENV.LOAD_WATCHLIST_USER || 900000103);
const QUICKPICKS_USER = Number(__ENV.LOAD_QUICKPICKS_USER || 900000102);
const MUTATING_USERS = [...new Set([RATING_USER, WATCHLIST_USER, QUICKPICKS_USER])];

// Cold Start is the one persona with a hard rule attached: the browser suite's
// PKCE and Quick Picks journeys own it and require it to be handed on with zero
// watched signals, and this harness's own cold-traffic assertion requires it to
// stay on the popularity fallback. A writer pointed at it would break both, so
// the run refuses to start rather than discovering it in a threshold.
if (MUTATING_USERS.includes(DISCOVER_COLD_USER)) {
  throw new Error(
    `persona ${DISCOVER_COLD_USER} is the cold-start persona and must never be mutated: ` +
      "the browser suite's Quick Picks journey asserts on its signal count and this " +
      "workload asserts it is served by the popularity fallback.",
  );
}

// Browse variants, rotated by iteration so one run covers search, genre, decade
// and both non-default sorts rather than measuring the same query 200 times.
// The page size is held constant so their percentiles stay comparable.
const BROWSE_VARIANTS = [
  { name: "default", query: "sort=title" },
  { name: "search", query: "sort=title&q=the" },
  { name: "genre", query: "sort=title&genre=Drama" },
  { name: "decade", query: "sort=title&year_from=1990&year_to=1999" },
  { name: "popular", query: "sort=popular" },
  { name: "newest", query: "sort=newest" },
];

const pageBlocking = new Trend("page_blocking_ms");
const pageJourney = new Trend("page_journey_ms");
// A revert that did not take is a correctness failure, not a slow request: the
// next run and the browser suite would inherit the dirt.
const unrevertedMutations = new Counter("unreverted_mutations");
const warmupRequests = new Counter("warmup_requests");
const warmupDuration = new Trend("warmup_duration_ms");

const profiles = {
  smoke: {
    duration: "45s",
    // Rates chosen so the whole mix lands near the ~55 requests/second the
    // pinned recommendation gate produces. The point is a realistic page mix
    // at a familiar total load, not a new capacity number.
    discover: { rate: 5, timeUnit: "1s", preAllocatedVUs: 4, maxVUs: 20 },
    browse: { rate: 4, timeUnit: "1s", preAllocatedVUs: 3, maxVUs: 15 },
    library: { rate: 3, timeUnit: "1s", preAllocatedVUs: 3, maxVUs: 12 },
  },
  // Same arrival rates, four times the window. That is deliberate and it is
  // what makes nightly the *enforcing* profile: a budget is only enforceable
  // against the workload it was measured on, and raising the rate here would
  // mean enforcing measured numbers against an unmeasured mix. What the longer
  // window buys is samples — the two serial writer journeys produce 23 and 45
  // per smoke run, which makes their p99 barely more than a maximum. Capacity
  // probing is `demo-load-nightly`'s job on the recommendation path.
  nightly: {
    duration: "3m",
    discover: { rate: 5, timeUnit: "1s", preAllocatedVUs: 6, maxVUs: 30 },
    browse: { rate: 4, timeUnit: "1s", preAllocatedVUs: 5, maxVUs: 25 },
    library: { rate: 3, timeUnit: "1s", preAllocatedVUs: 4, maxVUs: 20 },
  },
};

if (!(PROFILE in profiles)) {
  throw new Error(`Unknown LOAD_PROFILE ${PROFILE}`);
}

const selected = profiles[PROFILE];

// Both writers run a single virtual user on purpose. They are journeys, not
// throughput tests, and serialising them means an `expected_revision` conflict
// can only ever be the service disagreeing with itself — never two copies of
// the harness racing over the same persona and movie.
const SERIAL_WRITER = { preAllocatedVUs: 1, maxVUs: 1 };

export const options = {
  batchPerHost: 16,
  discardResponseBodies: false,
  scenarios: {
    discover: {
      executor: "constant-arrival-rate",
      exec: "discoverPage",
      duration: selected.duration,
      ...selected.discover,
    },
    browse: {
      executor: "constant-arrival-rate",
      exec: "browsePage",
      duration: selected.duration,
      ...selected.browse,
    },
    library: {
      executor: "constant-arrival-rate",
      exec: "libraryPage",
      duration: selected.duration,
      ...selected.library,
    },
    mutation: {
      executor: "constant-arrival-rate",
      exec: "mutationJourney",
      duration: selected.duration,
      rate: 1,
      timeUnit: "1s",
      ...SERIAL_WRITER,
    },
    quickpicks: {
      executor: "constant-arrival-rate",
      exec: "quickPicksJourney",
      duration: selected.duration,
      rate: 1,
      timeUnit: "2s",
      ...SERIAL_WRITER,
    },
  },
  thresholds: pageThresholds(),
  summaryTrendStats: ["avg", "min", "med", "p(50)", "p(95)", "p(99)", "max"],
};

// --- setup ------------------------------------------------------------------

export function setup() {
  const config = {
    keycloakUrl: KEYCLOAK_URL,
    realm: __ENV.KEYCLOAK_REALM || "demo",
    clientId: __ENV.KEYCLOAK_CLIENT_ID || "movielens-api",
    clientSecret: __ENV.KEYCLOAK_CLIENT_SECRET || "movielens-api-secret-dev-only",
    username: __ENV.KEYCLOAK_USERNAME || "demo",
    password: __ENV.KEYCLOAK_PASSWORD || "demo",
  };
  waitForReadiness(BASE_URL, Number(__ENV.READINESS_ATTEMPTS || 10));
  const auth = { config, ...mintAccessToken(config) };

  const snapshots = {};
  let catalogIds = [];
  for (const userId of MUTATING_USERS) {
    const catalog = readWholeCatalog(auth, userId);
    snapshots[userId] = catalog.states;
    catalogIds = catalog.ids;
  }
  const pools = {
    watchlist: pickUntouchedMovies(catalogIds, snapshots[WATCHLIST_USER], 16),
    rating: pickRatedMovies(snapshots[RATING_USER], 8),
  };
  if (pools.watchlist.length === 0 || pools.rating.length === 0) {
    throw new Error(
      `no usable mutation pool: persona ${WATCHLIST_USER} has ${pools.watchlist.length} ` +
        `untouched movies and persona ${RATING_USER} has ${pools.rating.length} rated ones. ` +
        "Run `make demo-seed` before measuring: the page workloads mutate real state.",
    );
  }
  // Browse opens a movie at the end of its journey. Resolving the id here keeps
  // the measured iteration to the requests the route actually makes.
  const detailMovieId = catalogIds[0];

  warmUp(auth);
  return { auth, snapshots, pools, detailMovieId };
}

/**
 * Prime every uvicorn worker for every route before the measured window.
 *
 * Same reasoning as the recommendation gate's warm-up (ADR 0010): each worker
 * is a process with its own JWKS cache, database pool and sidecar pool, and the
 * sidecar's feature cache is keyed by (tenant, user, candidate set). A page
 * workload widens the surface — catalog, library, taste profile and feature
 * reads each carry their own first-hit cost — so the warm-up walks the same
 * endpoints the scenarios do rather than only the recommendation path.
 */
function warmUp(auth) {
  const startedAt = Date.now();
  const params = {
    headers: authorizationHeaders(auth),
    tags: { endpoint: "warmup" },
    timeout: "15s",
  };
  const urls = [`${BASE_URL}/personas`];
  const personas = [
    ...new Set([DISCOVER_WARM_USER, DISCOVER_COLD_USER, LIBRARY_USER, ...MUTATING_USERS]),
  ];
  for (const userId of personas) {
    urls.push(
      `${BASE_URL}/users/${userId}/recommendations?limit=${DISCOVER_RECOMMENDATION_LIMIT}`,
      `${BASE_URL}/users/${userId}/history?limit=${DISCOVER_HISTORY_LIMIT}`,
      `${BASE_URL}/users/${userId}/library?tab=rated&sort=recent&limit=${LIBRARY_PAGE_SIZE}`,
      `${BASE_URL}/users/${userId}/taste-profile`,
      `${BASE_URL}/users/${userId}/features`,
      `${BASE_URL}/users/${userId}/audits?limit=${DISCOVER_AUDIT_LIMIT}`,
      `${BASE_URL}/users/${userId}/catalog?limit=${CATALOG_PAGE_SIZE}&sort=title`,
    );
  }
  // Two passes per worker: a connection stays pinned to one worker for its
  // lifetime, so a single pass can leave a worker cold on some endpoints.
  const rounds = 2 * API_WORKERS;
  let requests = 0;
  let slowest = 0;
  for (let round = 0; round < rounds; round++) {
    const responses = http.batch(urls.map((url) => ({ method: "GET", url, params })));
    requests += responses.length;
    for (const response of responses) {
      slowest = Math.max(slowest, response.timings.duration);
      if (response.status !== 200) {
        throw new Error(
          `warm-up failed: HTTP ${response.status} from ${response.request.url}. ` +
            "The load stack is not serving the seeded demo personas — check " +
            "`make demo-seed`, model-server, and feature-server before trusting any latency number.",
        );
      }
    }
  }
  const durationMs = Date.now() - startedAt;
  warmupRequests.add(requests);
  warmupDuration.add(durationMs);
  console.log(
    `page warm-up complete: rounds=${rounds} requests=${requests} workers=${API_WORKERS} ` +
      `slowest_ms=${slowest.toFixed(1)} duration_ms=${durationMs}`,
  );
}

// --- scenarios --------------------------------------------------------------

export function discoverPage(data) {
  const iteration = exec.scenario.iterationInTest;
  // Roughly one view in five is the cold-start persona, which is the ratio the
  // fallback deserves in a page mix: rare, but never zero, because it is a
  // different code path with a different cost.
  const cold = iteration % 5 === 4;
  const userId = cold ? DISCOVER_COLD_USER : DISCOVER_WARM_USER;
  const expected = cold ? POPULARITY_POLICY : LEARNED_POLICY;
  const traffic = cold ? "cold" : "warm";
  const journeyStart = Date.now();

  // All three block the server render (web/app/discover/page.tsx runs them in
  // one Promise.all), so the page costs the slowest of the three — including
  // `/personas`, which is on the critical path for nothing more than a display
  // label. Measuring it separately is how that shows up as a decision.
  const blockingStart = Date.now();
  const [recommendations, history, personas] = http.batch([
    request(
      data.auth,
      `${BASE_URL}/users/${userId}/recommendations?limit=${DISCOVER_RECOMMENDATION_LIMIT}`,
      { page: "discover", step: "recommendations", endpoint: "recommendations", traffic },
    ),
    request(data.auth, `${BASE_URL}/users/${userId}/history?limit=${DISCOVER_HISTORY_LIMIT}`, {
      page: "discover",
      step: "history",
      endpoint: "history",
    }),
    request(data.auth, `${BASE_URL}/personas`, {
      page: "discover",
      step: "personas",
      endpoint: "personas",
    }),
  ]);
  pageBlocking.add(Date.now() - blockingStart, { page: "discover" });

  check(
    recommendations,
    {
      "HTTP 200": (value) => value.status === 200,
      "auditable request id": (value) => typeof value.headers["X-Request-Id"] === "string",
      "serving policy matches the persona": (value) => policyMatches(value, expected),
      "non-empty recommendations": (value) => arrayLength(value, "items") > 0,
      // A page renders at most what it asked for; more would mean the API is
      // ignoring `limit`, which is how a grid turns into a scroll-jank bug.
      "recommendation page is bounded": (value) =>
        arrayLength(value, "items") <= DISCOVER_RECOMMENDATION_LIMIT,
      // Cards carry their own metadata. If they did not, the client would have
      // to fan out per card to fill the poster grid in, which is exactly the
      // pattern the catalog contract forbids.
      "cards carry self-contained metadata": (value) =>
        everyItem(
          value,
          (item) =>
            typeof item.title === "string" &&
            Array.isArray(item.genres) &&
            typeof item.metadata_source === "string" &&
            typeof item.reason === "string",
        ),
    },
    { page: "discover", step: "recommendations", traffic },
  );
  check(
    history,
    {
      "HTTP 200": (value) => value.status === 200,
      "history page is bounded": (value) =>
        arrayLength(value, "items") <= DISCOVER_HISTORY_LIMIT,
    },
    { page: "discover", step: "history" },
  );
  check(
    personas,
    {
      "HTTP 200": (value) => value.status === 200,
      "persona labels are available": (value) => arrayLength(value, "items") > 0,
    },
    { page: "discover", step: "personas" },
  );

  // Technical evidence is a disclosure, not part of the first render. It is
  // measured separately so a slow audit read can never hide inside — or be
  // hidden by — the page's blocking cost.
  const [audits, features] = http.batch([
    request(data.auth, `${BASE_URL}/users/${userId}/audits?limit=${DISCOVER_AUDIT_LIMIT}`, {
      page: "discover",
      step: "audits",
      endpoint: "audits",
      deferred: "true",
    }),
    request(data.auth, `${BASE_URL}/users/${userId}/features`, {
      page: "discover",
      step: "features",
      endpoint: "features",
      deferred: "true",
    }),
  ]);
  check(
    audits,
    {
      "HTTP 200": (value) => value.status === 200,
      // The panel's whole claim is provenance. An audit row without versions
      // and a correlation id cannot support it.
      "audit carries model provenance": (value) => {
        const items = value.json("items");
        if (!Array.isArray(items) || items.length === 0) {
          return false;
        }
        const first = items[0];
        return (
          typeof first.model_version === "string" &&
          typeof first.candidate_version === "string" &&
          typeof first.ranker_version === "string" &&
          typeof first.feature_version === "string" &&
          typeof first.correlation_id === "string"
        );
      },
    },
    { page: "discover", step: "audits" },
  );
  check(
    features,
    {
      // 503 is the documented degraded answer when the online store is
      // unreachable. It is a failure for this gate: the panel exists to show
      // real features, and an empty one is not evidence.
      "HTTP 200": (value) => value.status === 200,
      "features name their source": (value) => value.json("source") === "feast-redis",
    },
    { page: "discover", step: "features" },
  );
  pageJourney.add(Date.now() - journeyStart, { page: "discover" });
}

export function browsePage(data) {
  const iteration = exec.scenario.iterationInTest;
  const variant = BROWSE_VARIANTS[iteration % BROWSE_VARIANTS.length];
  const base = `${BASE_URL}/users/${BROWSE_USER}/catalog?limit=${CATALOG_PAGE_SIZE}&${variant.query}`;
  const journeyStart = Date.now();

  // Browse's server render makes no upstream call at all; the grid is a single
  // client fetch. Its blocking cost is therefore exactly this one request.
  const blockingStart = Date.now();
  const first = get(data.auth, base, {
    page: "browse",
    step: "catalog_first",
    endpoint: "catalog",
    variant: variant.name,
  });
  pageBlocking.add(Date.now() - blockingStart, { page: "browse" });

  const seen = collectIds(first);
  check(
    first,
    {
      "HTTP 200": (value) => value.status === 200,
      "catalog page is bounded": (value) =>
        arrayLength(value, "items") <= CATALOG_PAGE_SIZE &&
        CATALOG_PAGE_SIZE <= CATALOG_MAX_PAGE_SIZE,
      "catalog page declares continuation": (value) => {
        const info = value.json("page");
        return !!info && typeof info.has_more === "boolean";
      },
      "cards carry self-contained metadata": (value) =>
        everyItem(
          value,
          (item) =>
            typeof item.title === "string" &&
            Array.isArray(item.genres) &&
            typeof item.metadata_source === "string" &&
            typeof item.source_status === "string",
        ),
    },
    { page: "browse", step: "catalog_first", variant: variant.name },
  );

  let cursor = cursorOf(first);
  for (let step = 1; step <= 2; step++) {
    if (!cursor) {
      break;
    }
    const response = get(data.auth, `${base}&cursor=${encodeURIComponent(cursor)}`, {
      page: "browse",
      step: `catalog_next_${step}`,
      endpoint: "catalog",
      variant: variant.name,
    });
    const ids = collectIds(response);
    check(
      response,
      {
        "HTTP 200": (value) => value.status === 200,
        "continuation is bounded": (value) => arrayLength(value, "items") <= CATALOG_PAGE_SIZE,
        // Keyset pagination's whole promise: the next page is new movies, not
        // an offset that re-serves rows when the underlying set shifts.
        "continuation returns unseen movies": () =>
          ids.length > 0 && ids.every((id) => !seen.includes(id)),
      },
      { page: "browse", step: `catalog_next_${step}`, variant: variant.name },
    );
    for (const id of ids) {
      seen.push(id);
    }
    cursor = cursorOf(response);
  }

  // Opening a card is Browse's primary action, and the detail route's server
  // render is one blocking call. It belongs to this journey rather than to a
  // scenario of its own.
  const detail = get(
    data.auth,
    `${BASE_URL}/users/${BROWSE_USER}/movies/${data.detailMovieId}`,
    { page: "browse", step: "movie_detail", endpoint: "movie-detail" },
  );
  check(
    detail,
    {
      "HTTP 200": (value) => value.status === 200,
      // A movie with no poster or overview must still answer with a usable
      // record and say which parts are missing. Degraded metadata is a render
      // decision, not an error.
      "detail names its metadata completeness": (value) => {
        const item = value.json("item");
        return (
          !!item &&
          typeof item.title === "string" &&
          typeof item.metadata_source === "string" &&
          ["complete", "partial", "unavailable"].includes(item.source_status)
        );
      },
    },
    { page: "browse", step: "movie_detail" },
  );
  pageJourney.add(Date.now() - journeyStart, { page: "browse" });
}

export function libraryPage(data) {
  const base = `${BASE_URL}/users/${LIBRARY_USER}`;
  const journeyStart = Date.now();

  // The route's first render is the active collection plus the taste summary
  // plus the persona label, all three concurrent (web/app/library/page.tsx).
  const blockingStart = Date.now();
  const [rated, taste, personas] = http.batch([
    request(
      data.auth,
      `${base}/library?tab=rated&sort=recent&limit=${LIBRARY_PAGE_SIZE}`,
      { page: "library", step: "library_rated", endpoint: "library", tab: "rated" },
    ),
    request(data.auth, `${base}/taste-profile`, {
      page: "library",
      step: "taste_profile",
      endpoint: "taste-profile",
    }),
    request(data.auth, `${BASE_URL}/personas`, {
      page: "library",
      step: "personas",
      endpoint: "personas",
    }),
  ]);
  pageBlocking.add(Date.now() - blockingStart, { page: "library" });

  check(
    rated,
    {
      "HTTP 200": (value) => value.status === 200,
      "library page is bounded": (value) =>
        arrayLength(value, "items") <= LIBRARY_PAGE_SIZE &&
        LIBRARY_PAGE_SIZE <= LIBRARY_MAX_PAGE_SIZE,
      // Every tab's count arrives with every tab's page, which is what lets
      // the client label all three tabs without three extra round trips.
      "all three tab counts are returned": (value) => {
        const counts = value.json("counts");
        return (
          !!counts &&
          typeof counts.rated === "number" &&
          typeof counts.watchlist === "number" &&
          typeof counts.history === "number"
        );
      },
      "rated rows carry canonical state": (value) =>
        everyItem(value, (item) => !!item.state && typeof item.state.revision === "number"),
    },
    { page: "library", step: "library_rated", tab: "rated" },
  );
  check(
    taste,
    {
      "HTTP 200": (value) => value.status === 200,
      // The summary has to name where it came from and when: the route is not
      // allowed to imply a chart that updates the moment feedback lands.
      "taste profile names its source and freshness": (value) =>
        value.json("source") === "live-ratings-v1" &&
        typeof value.json("generated_at") === "string",
    },
    { page: "library", step: "taste_profile" },
  );
  check(
    personas,
    {
      "HTTP 200": (value) => value.status === 200,
      "persona labels are available": (value) => arrayLength(value, "items") > 0,
    },
    { page: "library", step: "personas" },
  );

  for (const tab of ["watchlist", "history"]) {
    const response = get(
      data.auth,
      `${base}/library?tab=${tab}&sort=recent&limit=${LIBRARY_PAGE_SIZE}`,
      { page: "library", step: `library_${tab}`, endpoint: "library", tab },
    );
    check(
      response,
      {
        "HTTP 200": (value) => value.status === 200,
        "tab echoes the requested collection": (value) => value.json("tab") === tab,
        "library page is bounded": (value) => arrayLength(value, "items") <= LIBRARY_PAGE_SIZE,
      },
      { page: "library", step: `library_${tab}`, tab },
    );
  }
  pageJourney.add(Date.now() - journeyStart, { page: "library" });
}

export function mutationJourney(data) {
  const iteration = exec.scenario.iterationInTest;
  // Alternate the two shapes a mutation takes: adding a state a movie did not
  // have, and changing a value it already had. They exercise different
  // branches of the revision guard and different client refresh paths.
  if (iteration % 2 === 0) {
    watchlistJourney(data, data.pools.watchlist[iteration % data.pools.watchlist.length]);
  } else {
    ratingJourney(data, data.pools.rating[iteration % data.pools.rating.length]);
  }
}

/** The persona a mutation step is scoped to, so tags and URLs cannot drift apart. */
function mutationUser(action) {
  return action === "rating" ? RATING_USER : WATCHLIST_USER;
}

function watchlistJourney(data, movieId) {
  const action = "watchlist";
  const user = mutationUser(action);
  const base = `${BASE_URL}/users/${user}/movies/${movieId}`;
  const journeyStart = Date.now();
  const blockingStart = Date.now();

  const before = get(data.auth, `${base}/state`, {
    page: "mutation",
    step: "state_read",
    endpoint: "movie-state",
    action,
  });
  const beforeRevision = revisionOf(before);
  check(
    before,
    { "HTTP 200": (value) => value.status === 200 },
    { page: "mutation", step: "state_read", action },
  );

  const key = uuid4();
  const added = mutate(data.auth, "PUT", `${base}/watchlist`, key, beforeRevision, null, {
    page: "mutation",
    step: "mutate",
    endpoint: "mutation",
    action,
  });
  // Deliberately the read plus the write. A real client already holds the
  // revision from the rendered page, so its tap costs only the mutation; this
  // harness has no page state and must fetch it first. Budgeting the pair keeps
  // the number honest about what was measured rather than quietly attributing
  // the harness's extra round trip to the product.
  pageBlocking.add(Date.now() - blockingStart, { page: "mutation" });
  check(
    added,
    {
      "HTTP 200": (value) => value.status === 200,
      "mutation committed the watchlist entry": (value) => stateOf(value).watchlisted_at !== null,
      "revision advanced past the expected one": (value) => revisionOf(value) > beforeRevision,
    },
    { page: "mutation", step: "mutate", action },
  );
  const addedRevision = revisionOf(added);

  // The same key again must be recognised as the same intent, not applied a
  // second time. This is the property a retrying client depends on, and the
  // Library surface reuses one key across retries of a single user intent.
  const replay = mutate(data.auth, "PUT", `${base}/watchlist`, key, null, null, {
    page: "mutation",
    step: "mutate_replay",
    endpoint: "mutation",
    action,
  });
  check(
    replay,
    {
      "HTTP 200": (value) => value.status === 200,
      "idempotent replay is recognised": (value) => value.json("replayed") === true,
      "idempotent replay does not advance the revision": (value) =>
        revisionOf(value) === addedRevision,
    },
    { page: "mutation", step: "mutate_replay", action },
  );

  // The mutation is not finished when the PUT returns; it is finished when the
  // next read sees it. Both reads a real client makes are measured: the
  // canonical state read, and the counts refresh the Library surface issues.
  const after = get(data.auth, `${base}/state`, {
    page: "mutation",
    step: "state_read_after",
    endpoint: "movie-state",
    action,
  });
  check(
    after,
    {
      "HTTP 200": (value) => value.status === 200,
      "immediate read observes the committed watchlist entry": (value) =>
        value.json("watchlisted_at") !== null && revisionOf(value) === addedRevision,
    },
    { page: "mutation", step: "state_read_after", action },
  );

  const counts = get(
    data.auth,
    `${BASE_URL}/users/${user}/library?tab=watchlist&sort=recent&limit=1`,
    { page: "mutation", step: "library_counts", endpoint: "library", action },
  );
  check(
    counts,
    {
      "HTTP 200": (value) => value.status === 200,
      "counts refresh observes the new watchlist entry": (value) => {
        const value_counts = value.json("counts");
        return !!value_counts && value_counts.watchlist >= 1;
      },
    },
    { page: "mutation", step: "library_counts", action },
  );

  const library = get(
    data.auth,
    `${BASE_URL}/users/${user}/library?tab=watchlist&sort=recent&limit=${LIBRARY_PAGE_SIZE}`,
    { page: "mutation", step: "library_read_after", endpoint: "library", action },
  );
  check(
    library,
    {
      "HTTP 200": (value) => value.status === 200,
      "library read observes the committed watchlist entry": (value) =>
        collectIds(value).includes(movieId),
    },
    { page: "mutation", step: "library_read_after", action },
  );

  const reverted = mutate(data.auth, "DELETE", `${base}/watchlist`, uuid4(), addedRevision, null, {
    page: "mutation",
    step: "revert",
    endpoint: "mutation",
    action,
  });
  check(
    reverted,
    { "HTTP 200": (value) => value.status === 200 },
    { page: "mutation", step: "revert", action },
  );

  const restored = get(data.auth, `${base}/state`, {
    page: "mutation",
    step: "state_read_final",
    endpoint: "movie-state",
    action,
  });
  const clean = restored.status === 200 && restored.json("watchlisted_at") === null;
  if (!clean) {
    unrevertedMutations.add(1, { action });
  }
  check(
    restored,
    {
      "HTTP 200": (value) => value.status === 200,
      "persona is restored": () => clean,
    },
    { page: "mutation", step: "state_read_final", action },
  );
  pageJourney.add(Date.now() - journeyStart, { page: "mutation" });
}

function ratingJourney(data, target) {
  const action = "rating";
  const user = mutationUser(action);
  const base = `${BASE_URL}/users/${user}/movies/${target.movie_id}`;
  // Half a star from the seeded value, clamped into the API's 0.5–5.0 range.
  // Small on purpose: this has to change the value without changing what the
  // persona means.
  const nextRating = target.rating >= 5.0 ? target.rating - 0.5 : target.rating + 0.5;
  const journeyStart = Date.now();
  const blockingStart = Date.now();

  const before = get(data.auth, `${base}/state`, {
    page: "mutation",
    step: "state_read",
    endpoint: "movie-state",
    action,
  });
  const beforeRevision = revisionOf(before);
  const originalRating = before.status === 200 ? before.json("rating") : null;
  const originalWatchedAt = before.status === 200 ? before.json("watched_at") : null;
  check(
    before,
    {
      "HTTP 200": (value) => value.status === 200,
      "rated movie already carries a rating": () => typeof originalRating === "number",
    },
    { page: "mutation", step: "state_read", action },
  );

  const key = uuid4();
  const changed = mutate(
    data.auth,
    "PUT",
    `${base}/rating`,
    key,
    beforeRevision,
    { rating: nextRating },
    { page: "mutation", step: "mutate", endpoint: "mutation", action },
  );
  pageBlocking.add(Date.now() - blockingStart, { page: "mutation" });
  check(
    changed,
    {
      "HTTP 200": (value) => value.status === 200,
      "mutation committed the new rating": (value) => stateOf(value).rating === nextRating,
      // Editing a star is not re-watching the movie. The preserved watched
      // timestamp is the evidence that these are independent states.
      "rating edit preserves the watched timestamp": (value) =>
        stateOf(value).watched_at === originalWatchedAt,
      "revision advanced past the expected one": (value) => revisionOf(value) > beforeRevision,
    },
    { page: "mutation", step: "mutate", action },
  );
  const changedRevision = revisionOf(changed);

  const replay = mutate(
    data.auth,
    "PUT",
    `${base}/rating`,
    key,
    null,
    { rating: nextRating },
    { page: "mutation", step: "mutate_replay", endpoint: "mutation", action },
  );
  check(
    replay,
    {
      "HTTP 200": (value) => value.status === 200,
      "idempotent replay is recognised": (value) => value.json("replayed") === true,
      "idempotent replay does not advance the revision": (value) =>
        revisionOf(value) === changedRevision,
    },
    { page: "mutation", step: "mutate_replay", action },
  );

  const after = get(data.auth, `${base}/state`, {
    page: "mutation",
    step: "state_read_after",
    endpoint: "movie-state",
    action,
  });
  check(
    after,
    {
      "HTTP 200": (value) => value.status === 200,
      "immediate read observes the committed rating": (value) =>
        value.json("rating") === nextRating && revisionOf(value) === changedRevision,
    },
    { page: "mutation", step: "state_read_after", action },
  );

  // A rating edit is the one action whose refresh also re-reads the taste
  // summary, because the summary is derived from live ratings.
  const [counts, taste] = http.batch([
    request(
      data.auth,
      `${BASE_URL}/users/${user}/library?tab=rated&sort=recent&limit=1`,
      { page: "mutation", step: "library_counts", endpoint: "library", action },
    ),
    request(data.auth, `${BASE_URL}/users/${user}/taste-profile`, {
      page: "mutation",
      step: "taste_refresh",
      endpoint: "taste-profile",
      action,
    }),
  ]);
  check(
    counts,
    {
      "HTTP 200": (value) => value.status === 200,
      "counts refresh returns all three collections": (value) => {
        const value_counts = value.json("counts");
        return !!value_counts && typeof value_counts.rated === "number";
      },
    },
    { page: "mutation", step: "library_counts", action },
  );
  check(
    taste,
    { "HTTP 200": (value) => value.status === 200 },
    { page: "mutation", step: "taste_refresh", action },
  );

  const library = get(
    data.auth,
    `${BASE_URL}/users/${user}/library?tab=rated&sort=recent&limit=${LIBRARY_PAGE_SIZE}`,
    { page: "mutation", step: "library_read_after", endpoint: "library", action },
  );
  check(
    library,
    {
      "HTTP 200": (value) => value.status === 200,
      "library read observes the committed rating": (value) => {
        const items = value.json("items");
        if (!Array.isArray(items)) {
          return false;
        }
        const row = items.find((item) => item.movie_id === target.movie_id);
        return !!row && row.state.rating === nextRating;
      },
    },
    { page: "mutation", step: "library_read_after", action },
  );

  const reverted = mutate(
    data.auth,
    "PUT",
    `${base}/rating`,
    uuid4(),
    changedRevision,
    { rating: target.rating },
    { page: "mutation", step: "revert", endpoint: "mutation", action },
  );
  check(
    reverted,
    { "HTTP 200": (value) => value.status === 200 },
    { page: "mutation", step: "revert", action },
  );

  const restored = get(data.auth, `${base}/state`, {
    page: "mutation",
    step: "state_read_final",
    endpoint: "movie-state",
    action,
  });
  const clean = restored.status === 200 && restored.json("rating") === target.rating;
  if (!clean) {
    unrevertedMutations.add(1, { action });
  }
  check(
    restored,
    {
      "HTTP 200": (value) => value.status === 200,
      "persona is restored": () => clean,
    },
    { page: "mutation", step: "state_read_final", action },
  );
  pageJourney.add(Date.now() - journeyStart, { page: "mutation" });
}

export function quickPicksJourney(data) {
  const user = QUICKPICKS_USER;
  const recommendationsUrl = `${BASE_URL}/users/${user}/recommendations?limit=${DISCOVER_RECOMMENDATION_LIMIT}`;
  const journeyStart = Date.now();

  const blockingStart = Date.now();
  const before = get(data.auth, recommendationsUrl, {
    page: "quickpicks",
    step: "recommendations_before",
    endpoint: "recommendations",
  });
  pageBlocking.add(Date.now() - blockingStart, { page: "quickpicks" });
  const queue = collectIds(before);
  check(
    before,
    {
      "HTTP 200": (value) => value.status === 200,
      "serving policy matches the persona": (value) => policyMatches(value, LEARNED_POLICY),
      "queue is non-empty": () => queue.length > 0,
    },
    { page: "quickpicks", step: "recommendations_before" },
  );
  if (queue.length === 0) {
    pageJourney.add(Date.now() - journeyStart, { page: "quickpicks" });
    return;
  }
  const movieId = queue[0];
  const base = `${BASE_URL}/users/${user}/movies/${movieId}`;

  const dismissed = mutate(data.auth, "PUT", `${base}/dismissal`, uuid4(), null, null, {
    page: "quickpicks",
    step: "dismiss",
    endpoint: "mutation",
    action: "dismissal",
  });
  check(
    dismissed,
    {
      "HTTP 200": (value) => value.status === 200,
      "dismissal is committed": (value) => stateOf(value).dismissed_at !== null,
    },
    { page: "quickpicks", step: "dismiss" },
  );
  const dismissedRevision = revisionOf(dismissed);

  const afterDismiss = get(data.auth, recommendationsUrl, {
    page: "quickpicks",
    step: "recommendations_after_dismiss",
    endpoint: "recommendations",
  });
  check(
    afterDismiss,
    {
      "HTTP 200": (value) => value.status === 200,
      // "Not for me" is durable suppression. If the title comes back on the
      // very next request the feature is decorative.
      "dismissed movie is gone from the queue": (value) => !collectIds(value).includes(movieId),
      "suppression is named in the policy": (value) => {
        const policy = value.json("serving_policy");
        return !!policy && policy.filter_policy === FILTER_POLICY;
      },
    },
    { page: "quickpicks", step: "recommendations_after_dismiss" },
  );

  const undone = mutate(data.auth, "DELETE", `${base}/dismissal`, uuid4(), dismissedRevision, null, {
    page: "quickpicks",
    step: "undo",
    endpoint: "mutation",
    action: "dismissal",
  });
  check(
    undone,
    {
      "HTTP 200": (value) => value.status === 200,
      "undo clears the dismissal": (value) => stateOf(value).dismissed_at === null,
    },
    { page: "quickpicks", step: "undo" },
  );

  const watched = mutate(data.auth, "PUT", `${base}/watched`, uuid4(), revisionOf(undone), null, {
    page: "quickpicks",
    step: "watched",
    endpoint: "mutation",
    action: "watched",
  });
  check(
    watched,
    {
      "HTTP 200": (value) => value.status === 200,
      "watched is committed": (value) => stateOf(value).watched_at !== null,
    },
    { page: "quickpicks", step: "watched" },
  );
  const watchedRevision = revisionOf(watched);

  const afterWatched = get(data.auth, recommendationsUrl, {
    page: "quickpicks",
    step: "recommendations_after_watched",
    endpoint: "recommendations",
  });
  check(
    afterWatched,
    {
      "HTTP 200": (value) => value.status === 200,
      "watched movie is excluded from the queue": (value) => !collectIds(value).includes(movieId),
      // The signal has to arrive coherently: still the learned policy, the
      // threshold unchanged, the count including what was just recorded, and
      // the exclusion policy named. A queue that quietly fell back to
      // popularity here would still answer 200 and still look fine.
      "serving policy stays coherent after the signal": (value) => {
        const policy = value.json("serving_policy");
        return (
          !!policy &&
          policy.name === LEARNED_POLICY &&
          policy.learned === true &&
          policy.threshold === COLD_START_THRESHOLD &&
          policy.filter_policy === FILTER_POLICY &&
          policy.positive_signal_count >= COLD_START_THRESHOLD &&
          policy.excluded_count >= policy.positive_signal_count
        );
      },
    },
    { page: "quickpicks", step: "recommendations_after_watched" },
  );

  const reverted = mutate(data.auth, "DELETE", `${base}/watched`, uuid4(), watchedRevision, null, {
    page: "quickpicks",
    step: "revert_watched",
    endpoint: "mutation",
    action: "watched",
  });
  const restoredState = stateOf(reverted);
  const clean =
    reverted.status === 200 &&
    restoredState.watched_at === null &&
    restoredState.dismissed_at === null;
  if (!clean) {
    unrevertedMutations.add(1, { action: "quickpicks" });
  }
  check(
    reverted,
    {
      "HTTP 200": (value) => value.status === 200,
      "persona is restored": () => clean,
    },
    { page: "quickpicks", step: "revert_watched" },
  );
  pageJourney.add(Date.now() - journeyStart, { page: "quickpicks" });
}

// --- teardown ---------------------------------------------------------------

/**
 * Put the mutated personas back the way setup found them.
 *
 * Every journey reverts inside its own iteration, so this should find nothing.
 * It exists because "should" is not a guarantee: an aborted run, a dropped
 * iteration, or a failed revert would otherwise leave shared demo personas
 * dirty for the next run and for the browser suite that uses the same fixtures.
 * Divergence is repaired and then reported as an error, because a run that
 * needed repairing did not measure what it claimed to.
 */
export function teardown(data) {
  assertColdStartUntouched(data.auth);
  const repaired = [];
  for (const userId of MUTATING_USERS) {
    const expected = data.snapshots[userId];
    const actual = readWholeCatalog(data.auth, userId).states;
    const movieIds = new Set([...Object.keys(expected), ...Object.keys(actual)]);
    for (const movieId of movieIds) {
      if (restoreMovie(data.auth, userId, movieId, expected[movieId], actual[movieId])) {
        repaired.push(`${userId}/${movieId}`);
      }
    }
  }
  if (repaired.length > 0) {
    throw new Error(
      `teardown had to repair ${repaired.length} persona states that an iteration should ` +
        `have reverted: ${repaired.slice(0, 10).join(", ")}. The personas are clean again, ` +
        "but this run mutated shared demo fixtures it did not put back on its own.",
    );
  }
}

const EMPTY_STATE = {
  watched_at: null,
  rating: null,
  watchlisted_at: null,
  dismissed_at: null,
};

/**
 * The cold persona still has nothing to learn from.
 *
 * The module-level guard proves no *configured* writer targets it. This proves
 * nothing wrote to it anyway — a mis-scoped URL, a stray pool entry, a fixture
 * that shifted underneath the run. It is one request and it protects two
 * separate contracts: this workload's cold-traffic assertion, and the browser
 * suite's Quick Picks journey, which owns this persona for its signal count.
 */
function assertColdStartUntouched(auth) {
  const response = http.get(
    `${BASE_URL}/users/${DISCOVER_COLD_USER}/recommendations?limit=1`,
    { headers: authorizationHeaders(auth), tags: { endpoint: "teardown" }, timeout: "15s" },
  );
  const policy = response.status === 200 ? response.json("serving_policy") : null;
  if (!policy || policy.name !== POPULARITY_POLICY || policy.positive_signal_count !== 0) {
    throw new Error(
      `cold-start persona ${DISCOVER_COLD_USER} did not survive the run untouched: ` +
        `HTTP ${response.status}, policy ${policy ? policy.name : "none"}, ` +
        `${policy ? policy.positive_signal_count : "?"} positive signals (expected popularity ` +
        "and 0). Something in this run wrote to the one persona nothing may write to.",
    );
  }
}

function restoreMovie(auth, userId, movieId, expected, actual) {
  const want = expected || EMPTY_STATE;
  const have = actual || EMPTY_STATE;
  const base = `${BASE_URL}/users/${userId}/movies/${movieId}`;
  let changed = false;

  // Clear the suppressing states first: the API refuses to watchlist a
  // dismissed or watched movie, so the order here is its transition order.
  if (!want.dismissed_at && have.dismissed_at) {
    repair(auth, "DELETE", `${base}/dismissal`);
    changed = true;
  }
  if (!want.watchlisted_at && have.watchlisted_at) {
    repair(auth, "DELETE", `${base}/watchlist`);
    changed = true;
  }
  if (!want.watched_at && have.watched_at) {
    // Removing history clears the rating with it, which is what the snapshot
    // says this movie should have.
    repair(auth, "DELETE", `${base}/watched`);
    changed = true;
  } else if (want.rating !== have.rating) {
    if (want.rating === null) {
      repair(auth, "DELETE", `${base}/rating`);
    } else {
      repair(auth, "PUT", `${base}/rating`, { rating: want.rating });
    }
    changed = true;
  }
  if (want.watched_at && !have.watched_at) {
    repair(auth, "PUT", `${base}/watched`);
    if (want.rating !== null) {
      repair(auth, "PUT", `${base}/rating`, { rating: want.rating });
    }
    changed = true;
  }
  if (want.watchlisted_at && !have.watchlisted_at) {
    repair(auth, "PUT", `${base}/watchlist`);
    changed = true;
  }
  if (want.dismissed_at && !have.dismissed_at) {
    repair(auth, "PUT", `${base}/dismissal`);
    changed = true;
  }
  return changed;
}

function repair(auth, method, url, body) {
  http.request(method, url, body ? JSON.stringify(body) : null, {
    headers: {
      ...authorizationHeaders(auth),
      "Content-Type": "application/json",
      "Idempotency-Key": uuid4(),
    },
    tags: { endpoint: "teardown" },
    timeout: "15s",
  });
}

// --- shared helpers ---------------------------------------------------------

function request(auth, url, tags) {
  return {
    method: "GET",
    url,
    params: { headers: authorizationHeaders(auth), tags, timeout: "5s" },
  };
}

function get(auth, url, tags) {
  return http.get(url, { headers: authorizationHeaders(auth), tags, timeout: "5s" });
}

function mutate(auth, method, url, idempotencyKey, expectedRevision, body, tags) {
  const separator = url.includes("?") ? "&" : "?";
  const target =
    expectedRevision === null || expectedRevision === undefined
      ? url
      : `${url}${separator}expected_revision=${expectedRevision}`;
  return http.request(method, target, body ? JSON.stringify(body) : null, {
    headers: {
      ...authorizationHeaders(auth),
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    tags,
    timeout: "5s",
  });
}

function policyMatches(response, expected) {
  if (response.status !== 200) {
    return false;
  }
  const policy = response.json("serving_policy");
  return (
    !!policy &&
    policy.name === expected &&
    policy.learned === (expected !== POPULARITY_POLICY) &&
    policy.threshold === COLD_START_THRESHOLD &&
    response.json("policy") === expected
  );
}

function arrayLength(response, field) {
  if (response.status !== 200) {
    return -1;
  }
  const value = response.json(field);
  return Array.isArray(value) ? value.length : -1;
}

function everyItem(response, predicate) {
  if (response.status !== 200) {
    return false;
  }
  const items = response.json("items");
  return Array.isArray(items) && items.length > 0 && items.every(predicate);
}

function collectIds(response) {
  if (response.status !== 200) {
    return [];
  }
  const items = response.json("items");
  return Array.isArray(items) ? items.map((item) => item.movie_id) : [];
}

function cursorOf(response) {
  if (response.status !== 200) {
    return null;
  }
  const info = response.json("page");
  return info && typeof info.next_cursor === "string" ? info.next_cursor : null;
}

/** The canonical state carried by a mutation response. */
function stateOf(response) {
  if (response.status !== 200) {
    return EMPTY_STATE;
  }
  return response.json("state") || EMPTY_STATE;
}

function revisionOf(response) {
  if (response.status !== 200) {
    return 0;
  }
  const body = response.json();
  // `GET .../state` answers HTTP 200 with a literal `null` for a movie the
  // persona has never touched. Revision 0 is what the API expects to be told
  // in that case.
  if (!body) {
    return 0;
  }
  const state = body.state || body;
  return typeof state.revision === "number" ? state.revision : 0;
}

/**
 * Page the whole demo catalog for one persona.
 *
 * The catalog is the only endpoint that reports every movie's state in one
 * pass, which is what makes it the right place to take a restore baseline: the
 * library tabs cannot see dismissals, and the state endpoint is per movie.
 */
function readWholeCatalog(auth, userId) {
  const states = {};
  const ids = [];
  let cursor = null;
  for (let page = 0; page < 16; page++) {
    const url =
      `${BASE_URL}/users/${userId}/catalog?limit=${CATALOG_MAX_PAGE_SIZE}&sort=title` +
      (cursor ? `&cursor=${encodeURIComponent(cursor)}` : "");
    const response = http.get(url, {
      headers: authorizationHeaders(auth),
      tags: { endpoint: "snapshot" },
      timeout: "15s",
    });
    if (response.status !== 200) {
      throw new Error(
        `could not snapshot persona ${userId}: HTTP ${response.status} from the catalog. ` +
          "The page workloads mutate real state and refuse to run without a baseline to restore.",
      );
    }
    for (const item of response.json("items")) {
      ids.push(item.movie_id);
      if (item.state) {
        states[item.movie_id] = {
          watched_at: item.state.watched_at,
          rating: item.state.rating,
          watchlisted_at: item.state.watchlisted_at,
          dismissed_at: item.state.dismissed_at,
        };
      }
    }
    cursor = cursorOf(response);
    if (!cursor) {
      break;
    }
  }
  return { states, ids };
}

/** Catalog movies this persona holds no state for — safe watchlist targets. */
function pickUntouchedMovies(catalogIds, snapshot, wanted) {
  const chosen = [];
  for (const movieId of catalogIds) {
    if (chosen.length >= wanted) {
      break;
    }
    if (!(movieId in snapshot)) {
      chosen.push(movieId);
    }
  }
  return chosen;
}

/** Movies the persona has already rated — safe rating-edit targets. */
function pickRatedMovies(snapshot, wanted) {
  const chosen = [];
  const movieIds = Object.keys(snapshot).sort((left, right) => Number(left) - Number(right));
  for (const movieId of movieIds) {
    if (chosen.length >= wanted) {
      break;
    }
    const state = snapshot[movieId];
    if (typeof state.rating === "number" && state.watched_at) {
      chosen.push({ movie_id: Number(movieId), rating: state.rating });
    }
  }
  return chosen;
}

// --- summary ----------------------------------------------------------------

export function handleSummary(data) {
  const breaches = collectBreaches(data);
  const verdict = {
    correctness_ok: breaches.correctness.length === 0,
    latency_ok: breaches.latency.length === 0,
    breached_correctness: breaches.correctness,
    breached_latency: breaches.latency,
  };
  const summary = {
    workload: "pages",
    profile: PROFILE,
    duration: selected.duration,
    verdict,
    warmup: {
      api_workers: API_WORKERS,
      requests: metricValues(data, "warmup_requests").count || 0,
      duration_ms: metricValues(data, "warmup_duration_ms").max || 0,
    },
    request_count: metricValues(data, "http_reqs").count || 0,
    throughput_rps: metricValues(data, "http_reqs").rate,
    // k6 divides request counts by the whole test-run duration, warm-up and
    // snapshotting included, so this is reported rather than inferred.
    test_run_duration_s: data.state ? data.state.testRunDurationMs / 1000 : null,
    dropped_iterations: metricValues(data, "dropped_iterations").count || 0,
    unreverted_mutations: metricValues(data, "unreverted_mutations").count || 0,
    pages: {},
    steps: {},
  };
  for (const page of Object.keys(PAGE_BUDGETS)) {
    summary.pages[page] = {
      blocking_ms: percentiles(metricValues(data, `page_blocking_ms{page:${page}}`)),
      journey_ms: percentiles(metricValues(data, `page_journey_ms{page:${page}}`)),
      check_rate: metricValues(data, `checks{page:${page}}`).rate,
      error_rate: metricValues(data, `http_req_failed{page:${page}}`).rate,
      budget: PAGE_BUDGETS[page],
    };
  }
  // Percentiles and the budget they were measured against. Sample counts are
  // deliberately absent: k6's trend summaries do not carry one, and a
  // hard-zero would read as "this step never ran". `summarize.py` derives the
  // real per-step counts from the sample stream into `steps.json`.
  for (const key of Object.keys(STEP_BUDGETS)) {
    const [page, step] = key.split(":");
    summary.steps[key] = {
      ...percentiles(metricValues(data, `http_req_duration{page:${page},step:${step}}`)),
      budget: STEP_BUDGETS[key],
    };
  }

  const rendered = `${JSON.stringify(summary, null, 2)}\n`;
  const notes = [];
  if (!verdict.correctness_ok) {
    notes.push(
      `CORRECTNESS BREACH: ${verdict.breached_correctness.join(", ")}. ` +
        "The API answered wrongly or a request failed; this is never advisory.",
    );
  }
  if (!verdict.latency_ok) {
    notes.push(`LATENCY BUDGET BREACH: ${verdict.breached_latency.join(", ")}.`);
  }
  const prefix = notes.length > 0 ? `${notes.join("\n")}\n` : "";
  const output = { stdout: `${prefix}${rendered}` };
  if (RESULTS_DIR) {
    output[`${RESULTS_DIR}/summary.json`] = rendered;
  }
  return output;
}

function collectBreaches(data) {
  const breaches = { correctness: [], latency: [] };
  for (const name of Object.keys(data.metrics)) {
    const metric = data.metrics[name];
    if (!metric || !metric.thresholds) {
      continue;
    }
    for (const source of Object.keys(metric.thresholds)) {
      const result = metric.thresholds[source];
      // k6 has reported this as `{ ok: boolean }` and, in older summaries, as a
      // bare boolean. Accept both rather than reading `undefined` as "passed" —
      // that would silently turn a breach into a green run.
      const ok = typeof result === "boolean" ? result : !!result && result.ok === true;
      if (!ok) {
        breaches[thresholdGroup(name)].push(`${name} ${source}`);
      }
    }
  }
  return breaches;
}

function percentiles(values) {
  return {
    p50: values["p(50)"],
    p95: values["p(95)"],
    p99: values["p(99)"],
    max: values.max,
  };
}

function metricValues(data, name) {
  const metric = data.metrics[name];
  return metric && metric.values ? metric.values : {};
}
