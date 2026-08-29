## Why

<!-- The problem or the decision. Not the diff — the reason the diff exists. -->

## What changed

<!-- The shape of the change. Name the modules, not every file. -->

## How it was verified

<!-- Name the gate that covers this, and say what you ran locally. Delete the
     rows that do not apply; do not leave a row claiming a gate you did not run. -->

| Gate | CI job | Ran locally |
| --- | --- | --- |
| Lint, format, types, OpenAPI drift | `lint` | |
| Unit | `test` | |
| Feature parity (offline == online) | `feature-parity` | |
| Tenant isolation | `tenant-isolation` | |
| k6 latency gate (p99 < 100 ms) | `synthetic-load-smoke` | |
| Service-backed browser journeys | `browser-auth-e2e` | |
| Fixture Playwright, Vitest, a11y | `frontend` | |
| Compose models | `demo-compose` | |
| Serving-bundle reproducibility | `serving-artifacts` | |
| Realm drift | `realm-drift` | |

<!-- If this touches src/serving/, quote the measured p50/p95/p99 from the load
     gate rather than saying it passed. -->

## Contracts touched

<!-- ADR added or amended; docs/api/openapi.json regenerated; a migration
     (state its revision and that it is additive); a documented frontend
     contract. "None" is a fine answer. -->

## Screenshots

<!-- Required for anything visual. Note the viewport. -->

---

- [ ] Conventional Commits, and the branch is `feat|fix|docs|chore/<short-description>`
- [ ] No secrets, tokens, or real credentials in the diff — this repository is public
- [ ] Migrations are additive only; production never runs a destructive one
- [ ] `docs/api/openapi.json` and `web/lib/api.generated.ts` regenerated if the API surface moved
- [ ] CLAUDE.md status updated if this changes scope or closes a tracked item
