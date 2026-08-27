// Thresholds for the post-deploy canary run of `recommendations.js`.
//
// Deliberately a separate module from `thresholds.js`, for the same reason
// `page_thresholds.js` is: that file carries the pinned p99 SLO gate
// (non-negotiables #4 and #11) and nothing outside a re-measured baseline may
// move it. Keeping the canary's weaker contract in its own file means it can
// never be edited into the gate by accident, and `git log thresholds.js` stays
// a truthful record that the numbers have never moved.
//
// What this run is for: proving a *deployment* serves correct, error-free
// recommendations end to end — real Keycloak tokens, real RLS-scoped audit
// writes, the learned path on warm personas and the popularity fallback on the
// cold one. It is a correctness probe that happens to arrive over 60 seconds,
// not a latency measurement.
//
// So it enforces exactly two things and no more:
//
//   checks rate==1          every response was correct, including the
//                           serving-policy assertions the checks make. A warm
//                           persona quietly degraded to popularity fails here,
//                           which is the failure a deploy is most likely to
//                           introduce.
//   http_req_failed rate==0 nothing errored or timed out.
//
// And it deliberately omits two:
//
//   p(99)<100   The SLO belongs to a controlled measurement: a known host, the
//               `run_gate.sh` wrapper, the CPU-steal join, and the
//               re-measure-once rule (ADR 0010, "measuring the service rather
//               than the runner"). A number collected at 5 arrivals/second
//               across a provider's private network, against containers sharing
//               a host with everything else on it, cannot be compared to the
//               accepted baseline — and a second, weaker place that appears to
//               define the SLO is how a threshold silently drifts. If the
//               deployed p99 is in question, run the pinned profile against a
//               dedicated environment and record it as a new baseline.
//
//   http_reqs rate>50   The achieved-throughput floor exists to catch a gate
//               that measured a trickle of traffic and called it a pass. This
//               profile *is* a trickle on purpose: 5 arrivals/second, because
//               every request writes an audit row to the production database
//               and a post-deploy check has no business generating the 55 rps
//               the CI gate does.
//
// Latency is still reported, so a deploy that got 10x slower is visible in the
// job log. It just is not this run's pass/fail contract.
//
// That reporting needs a line here to work at all, which is the one piece of
// k6 mechanics this file cannot leave implicit: a *tagged* sub-metric exists
// only if some threshold names it. `handleSummary` reads p50/p95/p99 off
// `http_req_duration{endpoint:recommendations}` specifically, so omitting it
// does not merely skip a gate — it makes the metric absent, and the canary's
// summary table renders `null` for all three percentiles. It did exactly that
// until 2026-08-27, which is how the omission was found: a rehearsal run
// produced `"latency_ms": {}` while this comment claimed the numbers were
// printed either way.
//
// `p(99)>=0` is the materialising line. A duration cannot be negative, so it
// cannot fail, and it is deliberately not a number anyone could misread as a
// second definition of the SLO.
export const canaryThresholds = {
  "checks{endpoint:recommendations}": ["rate==1"],
  "http_req_failed{endpoint:recommendations}": ["rate==0"],
  "http_req_duration{endpoint:recommendations}": ["p(99)>=0"],
};
