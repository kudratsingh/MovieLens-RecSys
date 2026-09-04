# Capacity, latency, and observability workstream

## Purpose

Keep model growth compatible with the approved service objective. Increased capacity is allowed
only when the complete serving path still meets quality, latency, reliability, and cost envelopes.

## Approved latency posture

The owner's 300–400 ms estimate is not the target. Preserve the stricter existing objectives:

- SASRec encoder p99 below 15 ms in its declared benchmark scope;
- authenticated end-to-end recommendation-service p99 below 100 ms under representative load.

Do not relax these automatically as parameter count, catalog size, traffic, or feature use grows.
Recover headroom through precomputation, ANN, batching, compilation, quantization, distillation,
caching, concurrency control, or additional capacity, then re-check quality and correctness.

## Latency budget

Before a serving-eligibility test, allocate the end-to-end p99 budget to named components:

- authentication, request validation, and rate limiting;
- user-history/online-feature retrieval;
- user encoding or embedding lookup;
- candidate generation and ANN/index lookup;
- candidate union, deduplication, filtering, and refill;
- feature hydration/transformation;
- ranker and re-ranker inference;
- serialization/network overhead and explicit safety margin.

Initial allocations are hypotheses, not hidden requirements. Measure them on the target deployment
shape and revise before freezing a release gate. Component budgets must add to less than 100 ms so
the remainder is visible margin.

## Benchmark protocol

Every reported latency result records:

- bundle and code/environment identity;
- hardware, accelerator, CPU architecture, memory, thread settings, and region;
- process/worker count, batch size, concurrency, and request rate;
- dataset/catalog/index sizes and feature payload shape;
- warm-up policy, cache state, run duration, and sample count;
- request mix across warm/cold/history buckets and candidate counts;
- p50, p90, p95, p99, maximum, error rate, and achieved throughput;
- component spans plus CPU/GPU utilization, memory, queueing, and saturation.

Report cold-start/model-load separately from steady-state. A local microbenchmark cannot certify the
authenticated service SLO, though it can reject a clearly slow design early.

## Load scenarios

At minimum test:

1. steady representative traffic with realistic user/history mix;
2. nominal peak with target concurrency;
3. short burst above peak to expose queueing;
4. cold process start and cold cache;
5. feature-store degradation and bounded timeout;
6. index/model loading during normal traffic;
7. worst supported history length and maximum candidate count;
8. fallback-heavy traffic and post-filter refill pressure.

The serving-eligibility gate uses an agreed representative scenario and fixed pass criteria. Stress
tests establish the operating boundary but do not redefine the SLO.

## Capacity model

Maintain a simple forecast per bundle:

- request rate and peak multiplier;
- service time by stage and estimated concurrency using Little's Law;
- CPU/GPU memory for models, embeddings, index, runtime, and headroom;
- CPU/GPU utilization at representative and peak load;
- online feature-store QPS, payload, storage, and network demand;
- replica count, autoscaling floor/ceiling, and startup time;
- cost per million recommendations and projected monthly range.

Calibrate the forecast against load tests. A mismatch beyond an agreed tolerance becomes a planning
defect to investigate, not a reason to omit cost/capacity evidence.

## Training resource policy

Local hardware and cloud GPUs are both permitted. Before a non-trivial run, log:

- why the hardware class is appropriate;
- expected wall-clock, accelerator-hours, and cost range;
- data/feature materialization time and storage;
- early-stop and checkpoint/restart behavior;
- maximum sweep size and concurrency;
- conditions that terminate an unproductive run.

“Typical budget” means measured, bounded iteration rather than an open-ended search. Start with
smoke and bounded pilots, update the estimate from observed throughput, then authorize full 25M
runs. Preserve failed-run telemetry so future estimates improve.

## Model-capacity decision ladder

When quality improves but latency fails, test interventions in an explicit order:

1. remove implementation overhead while preserving outputs;
2. precompute item-side work and cache stable user-side work;
3. tune ANN and batching within quality guardrails;
4. compile/export to an optimized runtime;
5. use mixed precision or quantization with parity checks;
6. distill or reduce architecture capacity and measure quality loss;
7. scale hardware/replicas if cost and operations remain acceptable.

Each change creates a new bundle/run identity. Never mix results from different optimizations under
one artifact label.

## Observability contract

### Model and routing

- active bundle/version by request and cohort;
- learned versus fallback route rate;
- source contribution, overlap, deduplication, and refill rate;
- candidate count before/after filters and empty-result rate;
- history-length and unknown-ID distributions;
- feature missing/default/staleness rates;
- score and rank-position distributions.

### Service

- throughput, errors, timeouts, retries, and saturation;
- end-to-end and component latency histograms;
- CPU/GPU utilization and memory;
- index/feature-store availability and cache-hit rate;
- bundle load/readiness duration and failures.

### Quality proxies and outcomes

- recommendation coverage/popularity/diversity by version and cohort;
- available delayed outcome rates under a fixed attribution window;
- synthetic canary/golden-request drift;
- offline-versus-online feature/output parity samples.

Attach bundle ID, routing cohort, and request trace ID to correlatable records while excluding raw
user history or other unnecessary sensitive payloads.

## Alert and rollback policy

Every metric needs owner, threshold/window, severity, destination, and runbook. Release-blocking or
rollback conditions should include sustained SLO/error failure, invalid bundle/schema, severe empty
candidate/fallback shift, feature freshness breach, resource exhaustion, and golden-canary mismatch.
Quality proxy drift normally triggers investigation; online outcome rollback thresholds require an
approved statistical and attribution contract.

## Exit criteria

- Both component and end-to-end latency protocols are reproducible.
- The full 25M bundle passes representative p99 and throughput gates with visible margin.
- Training runs have bounded cost/time estimates and restart/termination controls.
- Dashboards distinguish versions, cohorts, sources, routes, and failure reasons.
- Alerts have tested runbooks, and model capacity cannot bypass the unchanged SLO.
