# Movie-discovery frontend: UI finish-gate review

**Status:** Live. The current verdict and the standing criteria table.

**Last updated:** 2026-08-29 · **Latest pass:** 2026-08-28 (Seen re-run)

This is the written gate that [the testing strategy](testing-strategy.md#ui-finish-gate)
and the [Bundles 5–7 handoff](records/bundles-5-7-handoff.md#bundle-7--finish-gate-and-cutover)
require before the legacy dashboard is removed. The gate has been run four
times; each pass is kept verbatim in
[`records/finish-gate-passes.md`](records/finish-gate-passes.md), and this page
carries what is true now.

---

## Verdict

**HOLD — and moderated research with real participants is the only thing
standing between this document and PASS.**

Every criterion a reviewer can settle is settled and passing. The three blocking
items the first pass recorded were cleared by the 7D cutover (PR #65), and the
two re-runs since have moved no criterion. What is missing is not reviewer work
and cannot be substituted for by any: it is observed data from people who are
not the author.

This matters more than a checklist row. The gate's own protocol says persona
simulations "do not count as validation data", and one engineer walking the
tasks is an expert walkthrough, not a study. Recording PASS without the sessions
would be the one dishonest line in a document whose whole purpose is to be
honest about what has and has not been demonstrated.

## Criteria

Applied in the documented order. The `7A` column is the first pass; `Now` is the
state after the 7D cutover, re-confirmed by the 2026-08-27 sweep and the
2026-08-28 Seen re-run — every row was re-run each time, not only the ones that
moved.

| # | Criterion | 7A | Now |
|---|---|---|---|
| 1 | Product legibility | HOLD | **PASS** |
| 2 | Hierarchy | PASS | PASS |
| 3 | Pattern fit | PASS | PASS |
| 4 | States | PASS | PASS |
| 5 | Responsive behaviour | HOLD | **PASS** |
| 6 | Implementation fidelity | HOLD | **PASS** |
| 7 | Truthfulness | HOLD | **PASS** |
| — | Accessibility gate | PASS | PASS |
| — | Performance and reliability | PASS | PASS |
| — | **Moderated research** | Not met | **Not met — requires participant sessions (owner)** |

The evidence for each row is in
[`records/finish-gate-passes.md` §10.4](records/finish-gate-passes.md#104-criteria).

Worth knowing about row 5 and the accessibility gate: putting `/` into the
accessibility matrix for the first time failed four checks immediately, on a
screen that had shipped for months — a 1.31:1 primary-action label among them.
That is the argument for putting a surface in a gate rather than reasoning about
it, and it is written up in
[§10.5](records/finish-gate-passes.md#105-the-gate-failed-the-first-time-it-was-pointed-at-the-front-door).

## What converts this verdict

The whole remaining distance, from
[§10.8](records/finish-gate-passes.md#108-what-the-owner-must-run-to-convert-this-verdict):

- **4–5 movie-focused participants** and **3–4 technical reviewers**, with
  keyboard-only and small-screen coverage present in the mix.
- The seven discovery tasks in
  [§4.2](records/finish-gate-passes.md#42-moderated-tasks), run **against the
  current build**. Both re-runs since the cutover changed what a participant
  would see doing them — what a `Watchlist` press does, whether a poster exists,
  how Browse opens, and, since 2026-08-28, that task 6's tab is labelled **Seen**
  and carries search, filters and five rankings. Running the sessions against an
  older build would produce observed data about a product that no longer exists.
- Capture what the passes deliberately left blank: completion and abandonment,
  time on task, errors and recovery, movie scan count before a decision,
  feedback-semantics comprehension, spontaneous comments and confidence, and
  whether the ML evidence is discoverable but non-disruptive.

**Then** replace the moderated-tasks section with the observed data and re-record
the verdict. On the evidence in the passes, nothing else is outstanding — if the
sessions surface no new defect this becomes **PASS**, and retiring `/legacy`
becomes eligible as its own PR against the rollback documented in
[`README.md`](README.md#rolling-the-cutover-back).

## Open findings

None of these blocks the gate.

| Finding | Status |
|---|---|
| **N4** — Library spends the first mobile viewport on identity copy | Precondition delivered (the persona is named in the shell at every width); the layout change itself is deferred |
| **N5** — Browse restores a stored window instead of re-reading | Deliberate; unchanged |
| **N6** — the product has no persona picker | Owner decision. The design contract asks for a *labelled* persona, which the shell now delivers at every width; a picker is its own PR, gated behind the `demo-impersonator` role (ADR 0012). The named chips remain on `/legacy` |
| **N7** — unlayered base rules beat every Tailwind colour utility | **Closed 2026-08-29.** The element resets now sit in `@layer base` — they duplicated Tailwind's preflight, so they were deleted rather than wrapped — and colour and type utilities win again app-wide; the `.legacy-on-light` workaround is retired and a white-on-white `Retry` on the movie-detail error boundary is fixed with it. `:focus-visible` stays unlayered on purpose, so no utility can remove a focus ring |
| **One cross-tenant canary holds only on an empty database** | `test_user_endpoints_never_cross_tenant_boundary[recommendations]` relies on the canary title reaching a top-50 popularity list. Honest in CI, misleading against a seeded database — worth fixing before it is ever pointed at a deployment |

N1, N3, and the three cutover blockers B1–B3 are closed; the sweep pass records
what closed each one. The rate-limiting item that these passes carried as open
was closed on 2026-08-27 by [ADR 0014](../adr/0014-request-rate-limiting.md).

## Re-running the gate

```bash
# Static and unit
cd web && npm run lint && npm run typecheck && npm test

# Fixture-mode matrix, 390/768/1440 plus the 320px sweep
cd web && npm run test:e2e:ui

# Service-backed, against a stack rebuilt from the branch
make demo-up && make demo-seed && make demo-smoke
cd web && npm run test:e2e && npm run test:perf

# Contracts, Python, and the catalog census
make api-contract-check && make web-api-types-check
pytest tests/unit --ignore=tests/unit/test_twotower.py
REQUIRE_TENANT_ISOLATION_STACK=1 pytest tests/tenant_isolation
make catalog-verify

# Evidence
cd web && MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:sweep
```

In CI, the `frontend` job runs the visual and accessibility gate and the
`browser-auth-e2e` job runs the service-backed journeys against the
bypass-disabled Compose stack, so a pull request already covers everything above
except the evidence capture.

## The four passes

| Pass | Date | Base | What it recorded |
|---|---|---|---|
| [7A](records/finish-gate-passes.md#1-verdict) | 2026-08-21 | `876fd36` | HOLD on three cutover blockers; all five product routes passing |
| [7D cutover re-run](records/finish-gate-passes.md#10-re-run-after-cutover-7d) | — | after PR #65 | All three blockers cleared, all seven criteria passing; HOLD now only on the sessions |
| [Frontend sweep](records/finish-gate-passes.md#11-sweep-re-run-2026-08-27) | 2026-08-27 | `099ac3d` | Ten P0/P1 defects and six features from a QA sweep, code audit and UX research; verdict unmoved |
| [Seen re-run](records/finish-gate-passes.md#12-seen-re-run-2026-08-28) | 2026-08-28 | `b4cf076` | The Library's third tab became the Seen experience; verdict unmoved |

A later pass is appended rather than folded into an earlier one. That is why the
record is long, and it is deliberate: the sequence of what was believed, what was
run, and what that changed is the part worth keeping.
