# M7/M8 — Online experimentation, off-policy evaluation, exploration, and frontier models

## M7 — Online experimentation and off-policy evaluation

### Objective

Create evidence that connects a served policy to observable outcomes, then use it for safe
champion/challenger tests and bounded exploration. This package depends on Phase 6 routing and
event collection outside the model-only scope.

### Data contract prerequisites

Each exposure must identify:

- tenant, subject, request, session, timestamp, and experiment assignment;
- champion/challenger/shadow policy and every artifact/feature version;
- eligible candidates, final ranks, source contributions, exclusions, and scores;
- action chosen, action probability/propensity, and exploration policy;
- outcome event(s), their timestamps, attribution window, and censoring state.

An impression is not a click; absence of an event is not a negative until the attribution window
closes. Shadow predictions are logged but have no propensity as served actions.

### Stage A — Deterministic experiments

1. Stable `(tenant, subject)` assignment from a versioned seed.
2. Assignment persistence across requests and model restarts.
3. Sample-ratio-mismatch checks before outcome analysis.
4. Exposure/outcome join completeness and duplicate handling.
5. Primary metric, guardrails, sample-size assumptions, and stopping rule defined before launch.
6. Shadow execution that cannot change responses or exceed latency budget.
7. Instant per-tenant rollback to champion.

Synthetic fixtures must cover boundaries, tenant separation, changing seeds, shadow mode, and
rollback.

### Stage B — Off-policy estimators

Implement in increasing complexity:

- inverse propensity scoring (IPS);
- self-normalized IPS (SNIPS);
- doubly robust estimation when an outcome model is justified.

Validate on synthetic logged policies where ground truth is known. Report effective sample size,
weight distribution/clipping, confidence intervals, and estimator bias. Refuse inputs with missing
or invalid propensities.

### Stage C — Bounded exploration

Begin with one low-risk slot and a finite approved action set. Compare a non-contextual baseline
and Thompson sampling before broader contextual policies. Keep hard exclusions and safety rules
outside the bandit reward. Log propensities exactly and monitor regret/utility plus guardrails.

### Acceptance criteria

- SRM, join completeness, duplicate, and attribution-window checks pass before inference.
- Experiment configuration is immutable and pre-registered.
- Shadow mode has no response effect and stays within resource budgets.
- IPS/SNIPS/DR recover known synthetic effects within declared intervals.
- Exploration can be disabled immediately without an artifact rebuild.
- No cross-tenant pooling occurs without an explicit statistical and privacy decision.

### Stop conditions

- Traffic or outcome rate cannot meet sample requirements.
- Exposure/outcome joins or propensities are incomplete.
- Guardrails breach, SRM persists, or assignment changes unexpectedly.
- Offline estimator behavior is unstable under reasonable clipping/sensitivity analysis.

## M8 — Frontier capstone

### Objective

Decide whether a generative or foundation-style sequence model creates reusable representation
value that earlier rungs cannot achieve within MovieLens and the approved compute budget.

### Candidate questions

- Does semantic-ID generation improve retrieval beyond ANN over a sequence encoder?
- Does a longer unified event transformer improve multiple downstream tasks with one frozen user
  representation?
- Can pretrained text/content representations close the true cold-item gap?
- Is the project data/compute scale sufficient to make any result interpretable?

### Bounded spike agenda

1. Select one question, one baseline, and one architecture family in an ADR proposal.
2. Define a small-scale reproduction target from a primary paper.
3. Inventory data, tokenizer/semantic-ID, accelerator, storage, and licensing constraints.
4. Set a fixed spend/time envelope and terminate automatically at the cap.
5. Run representation and downstream-transfer ablations through the existing protocol.
6. Compare quality, training/inference cost, artifact size, reproducibility, and serving fit.
7. Decide: advance, archive as research, or keep as reading list.

### Acceptance criteria

- An approved Rung 7 ADR states why earlier models cannot answer the question.
- The experiment uses the same temporal/evaluation discipline and honest baselines.
- External pretrained artifacts and data have pinned versions and permitted use.
- Downstream value is measured, not inferred from retrieval loss alone.
- Cost and scaling claims are reported alongside quality.
- A negative result closes cleanly without pressure to productionize.

### Stop conditions

- MovieLens scale cannot distinguish the hypothesis.
- Required compute exceeds D-013.
- The approach wins only by using incomparable external data.
- It does not improve a downstream consumer enough to justify the new artifact/serving burden.

M8 is optional. Leaving it as an evidence-backed reading list is a valid program outcome.
