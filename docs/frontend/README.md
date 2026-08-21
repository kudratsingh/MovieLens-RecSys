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
- [Frontend system](frontend-system.md) — tokens, route/client boundaries, typed
  fixture semantics, the shared movie-state control family and write path, and
  automated checks.
- [Bundles 5–7 handoff](bundles-5-7-handoff.md) — exact post-Bundle-4 state,
  remaining vertical slices, implementation order, risks, and verification
  gates; Bundles 5–7 are explicitly not implemented in this handoff.
- [Backend readiness](backend-readiness.md) — source-audited capabilities,
  release blockers, proposed API boundaries, and frontend-safe claims.
- [Catalog and movie-detail contract](catalog-contract.md) — paging, local
  metadata, coverage, degradation, and Bundle 3 UI behavior.
- [Generated API contract](../api/README.md) — committed OpenAPI, generated
  TypeScript types, stable operation IDs, and CI drift checks.
- [Durable feedback and Library](library-feedback-contract.md) — migration,
  transition, pagination, idempotency, and truthful taste-summary contracts.
- [Testing strategy](testing-strategy.md) — research protocol, automated test
  pyramid, responsive evidence matrix, and the final PASS/HOLD finish gate.
- [Finish-gate review](finish-gate-review.md) — the written gate applied to the
  running product: what was run, the five-second and moderated-task
  walkthroughs, a verdict per criterion, and the recorded decision. The 7A pass
  recorded **HOLD** on three cutover items; the
  [re-run after the cutover](finish-gate-review.md#re-run-after-cutover-7d)
  clears all three and records **HOLD pending participant sessions** — every
  criterion a reviewer can settle now passes, and moderated research is the only
  thing left between that and PASS. Screenshot matrices, with per-file
  provenance, are in [`evidence/bundle-7a/`](evidence/bundle-7a/README.md) and
  [`evidence/bundle-7d/`](evidence/bundle-7d/README.md).
- [Baseline evidence](baseline-evidence.md) — current implementation evidence and
  the screenshot matrix that must be captured against the seeded demo.

## Rolling the cutover back

The movie-discovery product is what `/` serves. The pre-redesign dashboard is
still deployed at `/legacy`, and pointing the front door back at it is one
file, `web/app/page.tsx`. Written out in full, because a rollback that has
never been read is not a rollback — the destination is one line, and the other
two hunks are only the parameter and the import it stops needing:

```diff
 import { auth, signIn } from "@/auth";
-import { frontDoorHref } from "@/lib/navigation";
 import "./sign-in.css";

-export default async function Home({
-  searchParams,
-}: {
-  searchParams: Promise<{ user?: string | string[]; userId?: string | string[] }>;
-}) {
-  const [params, session] = await Promise.all([searchParams, auth()]);
+export default async function Home() {
+  const session = await auth();
   if (!session?.user || session.error) {
     return <SignInPage expired={session?.error === "RefreshAccessTokenError"} />;
   }

-  redirect(frontDoorHref(params));
+  redirect("/legacy");
 }
```

Nothing else has to move. `/legacy` renders the dashboard on its own, behind
the same session check as every other route, and the product routes keep
working for anyone who has a link to them. The reverse direction is the same
edit undone.

`/legacy` stays until the finish gate records a participant-backed PASS. The
handoff is explicit that the dashboard is removed **only after PASS**, in a
dedicated PR, with a documented rollback — this section is that document, and
the removal is still owed. Until then, treat the legacy route as deployed
surface: it is authenticated, it is in the service-backed journey, and its
serving-contract panel reports the policy the response carried rather than a
constant.

One consequence worth naming: the legacy dashboard is currently the only place
in the UI that offers the four demo personas by name. The product selects a
persona by URL (`?userId=` on Discover and Library, `?user=` on Browse, movie
detail, and Quick Picks). A persona switcher in the product shell is recorded
as a follow-up in the
[finish-gate review](finish-gate-review.md#re-run-after-cutover-7d), not as
work this cutover did.

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
