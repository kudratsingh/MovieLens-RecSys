# Frontend product documentation

This directory owns the product and delivery contract for the MovieLens
portfolio frontend. The frontend should feel like a movie-discovery product
while keeping the ML system inspectable through progressive disclosure.

## Documents

- [Product discovery](product-discovery.md) — users, jobs, current-state audit,
  research questions, reference patterns, and working assumptions.
- [Design contracts](design-contracts.md) — route-level first reads, actions,
  information hierarchy, responsive behavior, and forbidden defaults.
- [Implementation plan](implementation-plan.md) — sequenced frontend and backend
  bundles, dependencies, API gaps, and delivery exit criteria.
- [Backend readiness](backend-readiness.md) — source-audited capabilities,
  release blockers, proposed API boundaries, and frontend-safe claims.
- [Generated API contract](../api/README.md) — committed OpenAPI, generated
  TypeScript types, stable operation IDs, and CI drift checks.
- [Durable feedback and Library](library-feedback-contract.md) — migration,
  transition, pagination, idempotency, and truthful taste-summary contracts.
- [Testing strategy](testing-strategy.md) — research protocol, automated test
  pyramid, responsive evidence matrix, and the final PASS/HOLD finish gate.
- [Baseline evidence](baseline-evidence.md) — current implementation evidence and
  the screenshot matrix that must be captured against the seeded demo.

## Governing decisions

- [Frontend framework ADR](../adr/frontend/0001-frontend-framework.md) — Next.js
  16, React 19, TypeScript, and Tailwind CSS.
- [Movie-discovery experience ADR](../adr/frontend/0002-movie-discovery-experience.md)
  — product lens, information architecture, interaction model, and progressive
  disclosure of ML evidence.
- [Browser identity, feedback, and online freshness ADR](../adr/0012-browser-identity-feedback-and-online-freshness.md)
  — accepted ownership, feedback-state, mutation-durability, and model-semantics
  contract required by Library and Quick Picks.

Any change that alters the meaning of a rating, watched event, rejection, or
watchlist action belongs in a cross-cutting backend/model ADR rather than only
in frontend documentation.
