# M4 — Sequence-aware ranking

## Objective

Test whether the sequence representation improves the final top 10, first through the existing
LightGBM champion and only then through candidate-aware neural ranking. This is roadmap Rung 3 and
requires a new approved ADR before implementation.

## Hypothesis ladder

1. **H1 — Frozen sequence features help LightGBM.** Add a small, timestamp-correct feature set
   derived from the frozen sequence encoder.
2. **H2 — Candidate-to-history attention adds value.** DIN-like target attention beats aggregate
   sequence features.
3. **H3 — Transformer action modeling adds incremental value.** TransAct-like encoding beats DIN
   enough to justify extra cost.

Each hypothesis has its own stop rule. Do not implement H2 if H1 clears the desired gap; do not
implement H3 if H2 fails.

## Stage A — ADR and representation contract

The ADR must choose:

- whether the encoder is frozen, fine-tuned, or versioned separately;
- exact candidate-conditioned and user-only signals;
- historical computation and online feature parity mechanism;
- candidate batch size and latency budget;
- negative/candidate construction and ranker retraining policy;
- promotion metric, calibration diagnostics, and stop conditions.

Define a representation artifact that includes encoder/vocab identity and timestamp semantics.

## Stage B — LightGBM plus sequence features

Start with a narrow, interpretable set such as:

- user-sequence/candidate embedding dot product;
- cosine similarity to recent-position representation;
- candidate attention mass over recent history;
- recency-weighted maximum item similarity;
- sequence length/truncation/unknown counts as context, not proxies for relevance.

Build each feature point-in-time for training rows and identically online. Run ablations:

- current eight-feature LightGBM champion;
- champion plus user-only sequence features;
- champion plus candidate-conditioned sequence features;
- shuffled/frozen-random encoder control where informative.

Gate on the same NDCG@10 protocol and seeds. Report calibration and feature gain, but do not infer
causality from LightGBM importance.

## Stage C — DIN, conditional

If H1 does not resolve the measured ranking gap and attention remains plausible:

- encode candidate-to-history interactions with masks and recency;
- preserve strict temporal contexts and serving exclusions;
- train on the exact candidate distribution it will score;
- compare against LightGBM-plus-sequence, not only the old eight-feature model;
- measure batch latency at 100/500 candidate counts;
- report score calibration or a monotonic calibration layer if scores leave the ranker.

## Stage D — TransAct, conditional

Proceed only if DIN demonstrates candidate attention value and the remaining error is plausibly
sequential rather than data-limited. Keep the candidate set, labels, and loss fixed. Compare the
incremental benefit to added training/inference cost.

## Evaluation

Primary: overall NDCG@10 +3% relative and warm/cold guardrails from ADR 0001.

Required supporting evidence:

- recall@10, MAP or MRR if added consistently;
- history-size and target-popularity slices;
- candidate-source slices and score calibration;
- feature/attention missingness and unknown-item behavior;
- rolling temporal windows and seed aggregation;
- end-to-end p99 plus ranker-only p50/p95/p99;
- attention sanity checks presented as diagnostics, not explanations of truth.

## Acceptance criteria

- An accepted Rung 3 ADR and approved roadmap row exist before code.
- Feature/representation versions and timestamps are in protocol and serving manifests.
- Offline and online sequence features match on fixed entities and event times.
- Any promoted ranker beats the immediate incumbent, not a weaker historical baseline.
- Neural work is justified by the previous stage's measured failure mode.
- Exact artifacts pass save/load equality and model-side latency gates.
- Scorecard states which hypothesis won and which later stage was skipped.

## Stop conditions

- H1 clears the goal: stop before DIN and promote the simpler model if all gates pass.
- H1 fails and sequence controls show no order value: close Rung 3.
- DIN fails to beat LightGBM-plus-sequence: do not build TransAct.
- Any gain disappears on rolling windows or requires leakage-prone features: invalidate.
- Latency or full-candidate batching exceeds the fixed service budget: do not promote.

## Suggested PR shape

1. Rung 3 ADR and representation contract.
2. Point-in-time sequence feature pipeline and parity tests.
3. LightGBM ablation/result.
4. DIN implementation/result only if approved by Stage B outcome.
5. TransAct implementation/result only if approved by Stage C outcome.
6. Artifact, latency, gate, and scorecard for the winning stage.
