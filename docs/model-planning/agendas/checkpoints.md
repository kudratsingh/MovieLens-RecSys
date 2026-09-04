# Checkpoint agendas

## Work-package kickoff — 45 minutes

### Inputs

- governing ADR and roadmap row;
- current champion scorecard;
- package plan, dependency map, open decisions, and risk register;
- proposed PR breakdown and compute envelope.

### Agenda

1. Restate the one decision/outcome the package serves — 5 min.
2. Verify entry conditions and approval boundary — 5 min.
3. Review current evidence and strongest alternative explanation — 10 min.
4. Walk dependencies, external handoffs, and branch/worktree ownership — 5 min.
5. Agree phase gates, stop rules, and what will not be built — 10 min.
6. Assign first PRs and decision owners — 5 min.
7. Read back exit artifacts and next checkpoint — 5 min.

### Exit artifacts

- approved/updated experiment or implementation spec;
- owners for every open blocking decision;
- fixed stop rule and compute cap;
- first reviewable PR scope;
- no unresolved conflict with another worktree.

## Pre-run review — 30 minutes

### Inputs

- immutable experiment spec;
- protocol hash and baseline compatibility report;
- correctness test output;
- sample-size, memory, wall-clock, and storage projection;
- exact command and output destinations.

### Agenda

1. Can the experiment falsify its hypothesis? — 5 min.
2. Is the comparison identical on data, time, catalog, routing, exclusions, K, and slices? — 5 min.
3. Could target, future, equal-time, seen-item, or feature leakage remain? — 5 min.
4. Are controls and diagnostics sufficient to distinguish mechanism from popularity? — 5 min.
5. Are seeds, uncertainty, stop rule, test seal, and compute cap fixed? — 5 min.
6. Read the exact command and abort procedure — 5 min.

### Go criteria

- all required checkboxes are satisfied;
- any exception is written and approved before the run;
- output is traceable to spec/protocol/code/data identities;
- resource caps cannot silently expand.

## Results review — 45 minutes

### Inputs

- complete runs and validity report;
- baseline/control table and slice diagnostics;
- resource profile and artifact list;
- predeclared decision rule;
- draft `results.md` entry and scorecard.

### Agenda

1. Validate completeness and invalidate/supersede broken runs — 5 min.
2. Apply the predeclared rule before discussing narratives — 10 min.
3. Review seed/window uncertainty and slice failures — 10 min.
4. Review mechanism controls: simple baseline, shuffle/ablation, bias diagnostics — 10 min.
5. Decide advance, stop, narrow repeat, or inconclusive — 5 min.
6. Name exactly what the result does and does not authorize — 5 min.

### Exit artifacts

- result validity and decision;
- run IDs and hashes in `results.md`;
- scorecard and ADR/roadmap dated note;
- one next action or a clean closeout;
- no post-hoc grid expansion hidden inside the same experiment.

## Promotion review — 45 minutes

### Inputs

- promotion-eligible scorecard;
- stage-local and end-to-end gate results;
- manifest and MLflow artifact checksums;
- save/load equivalence, feature parity, latency, reliability, isolation, and audit evidence;
- current tenant champion and rollback predecessor.

### Agenda

1. Prove the artifact is the evaluated artifact — 5 min.
2. Review offline gates and any tolerated slice movement — 10 min.
3. Review online equivalence, fallback, exclusions, unknowns, and latency — 10 min.
4. Review registry assignment, atomicity, canary, and rollback — 10 min.
5. Confirm owner approval and affected tenants — 5 min.
6. Decide promote, hold, or reject; name verification window — 5 min.

### Hard blocks

- missing or mismatched artifact/run/protocol hash;
- failed or inconclusive gate;
- unapproved rung/decision;
- retraining during packaging;
- latency threshold breach;
- isolation, exclusion, audit, or fallback regression;
- no tested rollback artifact.

## Roadmap review — 60 minutes, after each rung

1. What did the last rung teach about data, model, and serving constraints? — 10 min.
2. What is the largest measured quality gap now? — 10 min.
3. Which next rung addresses that gap, and what cheaper baseline could falsify it? — 10 min.
4. Are labels, evaluation, compute, artifact, and online data ready? — 10 min.
5. Should a rung be approved, deferred, skipped, or kept as reading? — 10 min.
6. Update decision register, risks, roadmap row, and proposed ADR owner — 10 min.

The meeting does not approve code by enthusiasm. It produces an ADR proposal or a recorded skip.
