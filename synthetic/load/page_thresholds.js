// Budgets for the page-shaped workloads in `pages.js`.
//
// Deliberately a separate module from `thresholds.js`. That file carries the
// pinned recommendation SLO (non-negotiables #4 and #11) and nothing in this
// bundle may move it; splitting the files means a page budget can never be
// edited into the gate by accident.
//
// Two independent groups, because they fail for different reasons and deserve
// different enforcement:
//
//   correctness — checks pass, no request errors. Deterministic: a breach means
//                 the API answered wrongly, and no amount of CPU explains it.
//                 Always enforced, on PR CI and nightly alike.
//   latency     — the per-step and per-page budgets below. These move with the
//                 host, so `run_gate.sh` can be told to report rather than fail
//                 them (`PAGE_LATENCY_ENFORCED=false`). Whether they are
//                 enforced on PR CI is recorded in ADR 0010, not decided here.
//
// Budgets are p95/p99 in milliseconds, derived from the measured baselines in
// ADR 0010's "Page-shaped workloads and browser timing" note. The margin over
// the worst observed capped run is documented there; do not tighten one of
// these without re-recording a baseline, and never loosen one to make a red
// build green without writing down the repeatable evidence first.

// Per-step HTTP budgets, keyed "<page>:<step>", in milliseconds.
//
// Derivation, stated once so it can be reapplied rather than re-invented:
//
//   budget = 1.5x the worst value at least two runs corroborate, rounded up to
//            the next 10 ms
//
// The runs are four 45-second windows with `api-load` and `model-server` each
// capped to one CPU. "Corroborated" means the second-highest of the four, not
// the highest, and that is the load-bearing part. A capped container is stalled
// by CFS throttling dozens of times per run, and these journeys produce 23 to
// 46 samples per window — so their p99 is barely more than a maximum, and one
// stall can carry it. Sizing off the single worst reading once produced a
// 230 ms budget for a recommendation read whose median is 26 ms: arithmetic,
// and useless as a gate. Requiring two runs to agree keeps a hypervisor stall
// from buying permanent headroom, and it makes these budgets *tighter* than the
// naive rule rather than looser.
//
// The cap is a harsher environment than a shared runner rather than a model of
// one (ADR 0010), so these remain stress-bound budgets: generous enough that a
// new gate does not flake, tight enough to catch an order-of-magnitude
// regression. They get re-derived from runner data once the nightly run has
// produced some.
//
// Steps that hit the same endpoint inside the same page share one budget, set
// to the worst of them. Encoding the gap between two 23-sample percentiles as
// two different contracts would be recording noise as a promise.
export const STEP_BUDGETS = {
  "discover:recommendations": { p95: 60, p99: 120 },
  "discover:history": { p95: 30, p99: 60 },
  "discover:personas": { p95: 30, p99: 60 },
  "discover:audits": { p95: 30, p99: 80 },
  "discover:features": { p95: 40, p99: 100 },

  // The first page and a continuation are different queries: one seeks from
  // the start, the other from a decoded keyset cursor. The two continuations
  // share a budget because nothing distinguishes them but sample noise.
  "browse:catalog_first": { p95: 40, p99: 70 },
  "browse:catalog_next_1": { p95: 40, p99: 100 },
  "browse:catalog_next_2": { p95: 40, p99: 100 },
  "browse:movie_detail": { p95: 40, p99: 70 },

  "library:library_rated": { p95: 50, p99: 100 },
  "library:library_watchlist": { p95: 50, p99: 100 },
  "library:library_history": { p95: 50, p99: 100 },
  "library:taste_profile": { p95: 40, p99: 50 },
  "library:personas": { p95: 30, p99: 60 },

  "mutation:state_read": { p95: 60, p99: 90 },
  "mutation:state_read_after": { p95: 60, p99: 90 },
  "mutation:state_read_final": { p95: 60, p99: 90 },
  "mutation:mutate": { p95: 70, p99: 130 },
  "mutation:mutate_replay": { p95: 70, p99: 130 },
  "mutation:revert": { p95: 70, p99: 130 },
  "mutation:library_counts": { p95: 50, p99: 80 },
  "mutation:library_read_after": { p95: 50, p99: 80 },
  "mutation:taste_refresh": { p95: 80, p99: 90 },

  // Quick Picks re-requests recommendations immediately after changing the
  // state those recommendations are built from, so every one of its reads is a
  // deliberate cache miss in the model sidecar. That is the cost of the
  // feature, and it is budgeted higher than Discover's cached read rather than
  // averaged in with it.
  "quickpicks:recommendations_before": { p95: 90, p99: 140 },
  "quickpicks:recommendations_after_dismiss": { p95: 90, p99: 140 },
  "quickpicks:recommendations_after_watched": { p95: 90, p99: 140 },
  "quickpicks:dismiss": { p95: 100, p99: 110 },
  "quickpicks:undo": { p95: 100, p99: 110 },
  "quickpicks:watched": { p95: 100, p99: 110 },
  "quickpicks:revert_watched": { p95: 100, p99: 110 },
};

// Per-page budgets on the two custom trends `pages.js` records.
//
//   blocking — the calls a route must finish before it can render its
//              first-read object. This is what a page's perceived server cost
//              is actually made of: a parallel fan-out costs its slowest leg,
//              not the sum of its legs.
//   journey  — the whole modelled sequence, deferred technical panels, cursor
//              continuations, mutations and their revert included. A budget
//              here catches a step that got slower without moving the p99 of
//              any single request.
//
// Same derivation, with one floor: a page's p99 budget is never below 1.3x its
// p95 budget. On the low-sample writer journeys the two statistics land on top
// of each other, and a p99 contract that is really a p95 contract is a trap for
// whoever reads it next.
export const PAGE_BUDGETS = {
  discover: { blocking: { p95: 60, p99: 120 }, journey: { p95: 120, p99: 170 } },
  browse: { blocking: { p95: 40, p99: 70 }, journey: { p95: 150, p99: 240 } },
  library: { blocking: { p95: 40, p99: 80 }, journey: { p95: 120, p99: 180 } },
  mutation: { blocking: { p95: 110, p99: 210 }, journey: { p95: 180, p99: 310 } },
  quickpicks: { blocking: { p95: 70, p99: 140 }, journey: { p95: 260, p99: 340 } },
};

export const PAGES = Object.keys(PAGE_BUDGETS);

/** Every request must succeed and every assertion must hold, on every page. */
export function correctnessThresholds() {
  const thresholds = {};
  for (const page of PAGES) {
    thresholds[`checks{page:${page}}`] = ["rate==1"];
    thresholds[`http_req_failed{page:${page}}`] = ["rate==0"];
  }
  // A revert that silently failed would leave the shared demo personas dirty
  // for the next run and for the browser suite, so it is a gate, not a log
  // line. `pages.js` increments this whenever teardown had to repair state it
  // expected an iteration to have already put back.
  thresholds["unreverted_mutations"] = ["count==0"];
  return thresholds;
}

/** The p95/p99 budgets, which the wrapper may be told to report rather than fail. */
export function latencyThresholds() {
  const thresholds = {};
  for (const [key, budget] of Object.entries(STEP_BUDGETS)) {
    const [page, step] = key.split(":");
    thresholds[`http_req_duration{page:${page},step:${step}}`] = [
      `p(95)<${budget.p95}`,
      `p(99)<${budget.p99}`,
    ];
  }
  for (const [page, budget] of Object.entries(PAGE_BUDGETS)) {
    thresholds[`page_blocking_ms{page:${page}}`] = [
      `p(95)<${budget.blocking.p95}`,
      `p(99)<${budget.blocking.p99}`,
    ];
    thresholds[`page_journey_ms{page:${page}}`] = [
      `p(95)<${budget.journey.p95}`,
      `p(99)<${budget.journey.p99}`,
    ];
  }
  return thresholds;
}

export function pageThresholds() {
  return { ...correctnessThresholds(), ...latencyThresholds() };
}

/**
 * Which group a breached metric belongs to.
 *
 * k6's exit status cannot say "the budgets slipped but the API is correct",
 * which is exactly the distinction PR CI needs while these budgets are new.
 * `pages.js` classifies each breach with this and writes the verdict into
 * `summary.json`; `run_gate.sh` turns that into the exit code.
 */
export function thresholdGroup(metricName) {
  return metricName.startsWith("checks") ||
    metricName.startsWith("http_req_failed") ||
    metricName.startsWith("unreverted_mutations")
    ? "correctness"
    : "latency";
}
