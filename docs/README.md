# Documentation

A multi-tenant, authenticated two-stage movie recommender: candidate retrieval,
a LightGBM ranker, a Feast/Redis feature store, Postgres row-level security,
Keycloak auth, a Next.js product on top, and a measured p99 SLO underneath. This
page is the map.

## A reading path

If you are here to judge whether the engineering holds up, read in this order.

1. **[`../README.md`](../README.md)** — what the project is, the stack, and the
   current phase.
2. **[`architecture.md`](architecture.md)** — the system in one document: the
   offline and online paths, and how the pieces fit. Diagram sources and
   rendered SVGs are in [`diagrams/`](diagrams/).
3. **[`modeling-roadmap.md`](modeling-roadmap.md)** — the model ladder: where the
   models are today, the rungs from here to a Netflix-class stack, and the
   approval each rung needs before it starts.
4. **[`adr/README.md`](adr/README.md)** — the decision records. It carries a
   reading order of its own; the short version is 0001 (the evaluation
   contract), 0003 (why two stages), 0006 (the retrieval model), 0008 (tenant
   isolation), then 0013 if what you care about is how this runs off a laptop.
   ADRs are never rewritten — a correction is a dated note appended to one.
5. **[`api/overview.md`](api/overview.md)** — every path and method, the auth
   rules, the rate-limit and correlation headers, and a worked recommendation
   response. [`api/README.md`](api/README.md) covers the committed OpenAPI
   artifact and its drift checks.
6. **[`demo-runbook.md`](demo-runbook.md)** — start the whole stack from a clean
   checkout, seed it, and walk it. The fastest way to see the claims above
   running.
7. **[`frontend/README.md`](frontend/README.md)** — the product: design
   contracts, the frontend system, the surface contracts, and the evidence
   index.
8. **[`frontend/finish-gate-review.md`](frontend/finish-gate-review.md)** — the
   written UI gate and its current verdict, which is HOLD pending moderated
   participant sessions.
9. **[`deployment-runbook.md`](deployment-runbook.md)** and
   **[ADR 0013](adr/0013-production-deployment-target.md)** — the deployment:
   one Hetzner CX22 running `docker-compose.prod.yml`, images from GHCR, a
   deploy that rolls itself back when verification fails. Specified and
   rehearsed end to end; **not yet provisioned**.
10. **[`eda.md`](eda.md)** — the dataset, characterised: scale, sparsity, the
   popularity tail, the temporal split as it lands on real data, and cold-start
   sizing.
11. **[`results.md`](results.md)** — the measured offline numbers: baselines,
    candidate stage, ranker and the cold-start cohort, each with its run, date,
    machine and caveats.

## By subject

| Document | What it holds |
|---|---|
| [`architecture.md`](architecture.md) | The system overview, with diagrams |
| [`modeling-roadmap.md`](modeling-roadmap.md) | The model ladder and its decision log — every rung needs approval before it starts |
| [`adr/`](adr/README.md) | Backend and cross-cutting ADRs on the flat numeric line; frontend ADRs under [`adr/frontend/`](adr/frontend/) |
| [`api/`](api/README.md) | The generated OpenAPI contract, how it is checked, and a readable [overview](api/overview.md) of the surface |
| [`frontend/`](frontend/README.md) | Product and delivery docs, surface contracts, the finish gate, and the [evidence index](frontend/evidence/README.md) |
| [`results.md`](results.md) | The measured offline results table, every figure with its MLflow run and wall-clock |
| [`cold-start-routing-decision.md`](cold-start-routing-decision.md) | Both cold-start routing policies measured side by side, and the ADR 0001 decision the numbers put to the owner |
| [`promotion-gate-slice-decision.md`](promotion-gate-slice-decision.md) | Which holdout slice the +3% promotion gate reads, and what each option would have said about the ranker |
| [`demo-runbook.md`](demo-runbook.md) | Clean-checkout startup, seeding, the walkthrough, the audit and latency proofs, reset, troubleshooting |
| [`deployment-runbook.md`](deployment-runbook.md) | The machine, DNS, host bootstrap, secrets, the one-time SQL, the first deploy, verify, rollback, backups and the restore drill, and §14's plain list of what the deployment does not do |
| [`production-readiness-review.md`](production-readiness-review.md) | The pre-deployment gap review and the rehearsal record. A record, banner and all, but a useful one |
| [`eda.md`](eda.md) | Exploratory data analysis on MovieLens 25M |
| [`records/`](records/README.md) | Documents that were accurate on a date and are kept for the reasoning, not the status |

Two READMEs outside this directory belong to the same map:
[`../synthetic/README.md`](../synthetic/README.md) for the load, persona, smoke
and tenant-isolation harnesses, and [`../infra/README.md`](../infra/README.md)
for the images and the operational scripts.

## What a record is

[`records/`](records/README.md) and
[`frontend/records/`](frontend/records/README.md) hold documents that describe a
state of the world rather than the system: delivery plans that have been
delivered, handoffs that have been taken up, evidence captured before a redesign.
Each carries a banner naming the date it was accurate on and what supersedes it,
and none is maintained.

They are kept rather than deleted because the reasoning in them is the useful
part, and editing a record until it agrees with `main` destroys the only thing it
was good for. An ADR is not a record in this sense: it is a live document that is
never rewritten, and corrections arrive as dated notes.

## What is claimed, and what is not

Phases 1 and 2 are complete; Phase 3 is in progress. The parts of Phase 3 that
are still open are listed at the end of the status section in
[`../CLAUDE.md`](../CLAUDE.md) rather than left to be discovered — cold-start
cohorts, per-tenant champion routing, generic request audits, and the
Feast-backed training refactor. The deployment is specified and rehearsed but
the machine does not exist yet, the dev and staging Compose environments exist
but neither is deployed anywhere either, and the frontend finish gate holds on
participant research.

Documents here say which of those they describe. Where one goes stale, the fix is
a dated correction rather than a quiet edit.
