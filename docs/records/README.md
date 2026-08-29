# Records

Documents in this directory were accurate on the date each one names, and are
kept because the reasoning in them is worth keeping. **They are not maintained.**
Where a record has a living successor, its banner says which document that is.

The distinction is deliberate. A plan that argued for a particular delivery order
is still the best explanation of why the work landed in that order, even after
every item on it is done — but it is a bad thing to read for the current state of
the system, and worse to quietly edit until it agrees with `main`. Editing a
record until it is current destroys the only thing it was good for.

| Record | Accurate as of | Superseded by |
|---|---|---|
| [Working demo plan](demo-plan.md) | 2026-08-21 (`c25cb69`, PR #45) | [`demo-runbook.md`](../demo-runbook.md) |
| [MVP release and deployment handoff](mvp-release-deployment-handoff.md) | 2026-08-27 (`4c74f0c`, PR #68) | [`deployment-runbook.md`](../deployment-runbook.md), [ADR 0013](../adr/0013-production-deployment-target.md) |
| [Release serving fix handoff](release-serving-fix-handoff.md) | 2026-08-27 (closed by PR #69) | [ADR 0010](../adr/0010-synthetic-load-k6.md), 2026-08-26 notes |

Two documents that read like records live elsewhere on purpose:

- [`production-readiness-review.md`](../production-readiness-review.md) stays at
  the top level. It is a record — it carries a banner saying so — but the gap
  analysis in it is one of the more useful things in the repository for an
  outside reader, and burying it here would be the wrong trade.
- Architecture decision records are not records in this sense. An ADR is a live
  document that is never rewritten: corrections arrive as dated notes appended
  to it. See [`../adr/README.md`](../adr/README.md).

Frontend records have their own directory:
[`../frontend/records/`](../frontend/records/README.md).
