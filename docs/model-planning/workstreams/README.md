# Cross-cutting model workstreams

Phase plans describe when work happens. These documents define the contracts that remain stable
across phases.

- [`evaluation-and-gates.md`](evaluation-and-gates.md) — protocol identity, metric definitions,
  uncertainty, rolling backtests, and promotion decisions.
- [`data-features-and-labels.md`](data-features-and-labels.md) — raw/derived snapshots, temporal
  examples, sampling, Feast boundaries, labels, and data-quality gates.
- [`artifacts-registry-and-serving.md`](artifacts-registry-and-serving.md) — exact artifacts,
  manifest v2, lifecycle states, registration, promotion, and rollback.
- [`capacity-and-observability.md`](capacity-and-observability.md) — training budgets, cloud/local
  selection, inference budgets, load scaling, and model-health signals.
- [`worktree-and-run-safety.md`](worktree-and-run-safety.md) — isolation from other sessions,
  resource coordination, and safe integration rules.

When a workstream proposal becomes a lasting architecture choice, record it in an ADR. These files
are implementation blueprints and checklists, not substitutes for decisions.
