# Model-work agendas

Use these agendas at decision boundaries. One person may fill every role, but every perspective
must still be answered explicitly.

- [`checkpoints.md`](checkpoints.md) contains kickoff, pre-run, results, promotion, and quarterly
  roadmap reviews.
- [`immediate-execution.md`](immediate-execution.md) lays out the concrete M0/M1 session order
  from branch reconciliation through the conditional SASRec full-data verdict.
- An agenda is complete only when its exit artifacts exist; “discussed” is not an outcome.
- Decisions that change labels, metrics, architecture, or promotion rules go to an ADR.
- Experiment-level choices go in the immutable experiment spec.
- Measurements go in `docs/results.md` and a scorecard.

Suggested roles:

- **Owner/decision maker:** approves rungs, utility, compute, and promotion.
- **Model author:** explains mechanism, implementation, and expected failure mode.
- **Evaluation reviewer:** challenges leakage, comparability, uncertainty, and test use.
- **Serving reviewer:** checks artifacts, parity, latency, fallback, and rollback.
- **Recorder:** captures decisions, owners, due checkpoints, and artifact links.
