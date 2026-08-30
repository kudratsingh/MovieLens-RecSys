# Frontend product documentation

This directory owns the product and delivery contract for the MovieLens
portfolio frontend. The frontend should feel like a movie-discovery product
while keeping the ML system inspectable through progressive disclosure.

**Status:** Bundles 0–7 delivered. The cutover is done — `/` serves the product
and the pre-redesign dashboard is retained at `/legacy` with the rollback
written out [below](#rolling-the-cutover-back). The
[finish-gate review](finish-gate-review.md) passes all seven criteria after the
cutover; the remaining HOLD is moderated research with real participants, and
legacy removal waits on that PASS.

## Documents

### Product and delivery

- [Design contracts](design-contracts.md) — route-level first reads, actions,
  information hierarchy, responsive behavior, and forbidden defaults.
- [Implementation plan](implementation-plan.md) — sequenced frontend and backend
  bundles, dependencies, API gaps, and delivery exit criteria.
- [Frontend system](frontend-system.md) — tokens, route/client boundaries, typed
  fixture semantics, the shared movie-state control family and write path, and
  automated checks.
- [Backend readiness](backend-readiness.md) — source-audited capabilities,
  release blockers, proposed API boundaries, and frontend-safe claims.

### Surface contracts

- [Catalog and movie-detail contract](catalog-contract.md) — paging, local
  metadata, coverage, degradation, and Browse/detail UI behavior.
- [Durable feedback and Library](library-feedback-contract.md) — migration,
  transition, pagination, idempotency, and truthful taste-summary contracts.
- [The Seen experience](seen-contract.md) — the Library's third tab: search,
  genre and year filters, five rankings with fingerprint-bound cursors, an exact
  `matched` count, and the spotlight that walks the same filtered list.
- [Generated API contract](../api/README.md) — committed OpenAPI, generated
  TypeScript types, stable operation IDs, and CI drift checks. A path-by-path
  tour of the surface is in [`api/overview.md`](../api/overview.md).

### Verification

- [Testing strategy](testing-strategy.md) — research protocol, automated test
  pyramid, responsive evidence matrix, and the final PASS/HOLD finish gate.
- [Finish-gate review](finish-gate-review.md) — the current verdict, the seven
  criteria and their state, the open non-blocking findings, and how to re-run
  the gate. It records **HOLD pending participant sessions**: every criterion a
  reviewer can settle passes, and moderated research is the only thing left
  between that and PASS.
- [Evidence index](evidence/README.md) — every screenshot matrix with its date,
  build, routes, viewports, and whether it was captured in fixture mode or
  against the seeded stack.

### Records

Not maintained; kept because the reasoning is worth keeping. Index and banners
in [`records/README.md`](records/README.md).

- [Finish-gate passes](records/finish-gate-passes.md) — the four dated passes
  verbatim, which is the evidence the verdict above rests on.
- [Product discovery](records/product-discovery.md) — users, jobs, current-state
  audit, research questions, reference patterns, and working assumptions. Its
  seven tasks are the ones the outstanding moderated sessions run.
- [Bundles 5–7 handoff](records/bundles-5-7-handoff.md) — the post-Bundle-4
  state and the remaining vertical slices, written before they were built.
- [Baseline evidence](records/baseline-evidence.md) — the pre-redesign
  screenshot matrix, captured so the finish gate had a before to compare against.

## Rolling the cutover back

The movie-discovery product is what `/` serves. The pre-redesign dashboard is
still deployed at `/legacy`, and pointing the front door back at it is one file,
`web/app/page.tsx`. Written out in full, because a rollback that has never been
read is not a rollback. It is two hunks: the destination, and the one import
that stops being used.

Verified by applying it to `web/app/page.tsx` as of `6af86a5` and diffing:

```diff
 import { auth, signIn } from "@/auth";
-import { frontDoorHref, safeSignInReturn, signInDestination } from "@/lib/navigation";
+import { safeSignInReturn, signInDestination } from "@/lib/navigation";
 import "./sign-in.css";
@@
   }

-  redirect(frontDoorHref(params));
+  redirect("/legacy");
 }
```

Note what does **not** move, because an earlier version of this section got it
wrong and would have deleted a binding still in use. `searchParams`, `params`,
`safeSignInReturn` and `signInDestination` all stay: the signed-out door reads
`?next=` to carry a bounced viewer's destination through sign-in, and that is
independent of where a signed-in viewer lands. Only `frontDoorHref` becomes
unused. The `user` and `userId` keys in the `searchParams` type also stop being
read, but they are type members rather than bindings — leaving them costs
nothing and keeps the rollback to two hunks.

Nothing else has to move. `/legacy` renders the dashboard on its own, behind the
same session check as every other route, and the product routes keep working for
anyone who has a link to them. The reverse direction is the same edit undone.

`/legacy` stays until the finish gate records a participant-backed PASS. The
handoff is explicit that the dashboard is removed **only after PASS**, in a
dedicated PR, with a documented rollback — this section is that document, and
the removal is still owed. Until then, treat the legacy route as deployed
surface: it is authenticated, it is in the service-backed journey, and its
serving-contract panel reports the policy the response carried rather than a
constant.

**The TMDB attribution no longer blocks the removal.** It used to: the mark and
the required non-endorsement sentence existed in one file,
`components/legacy/recommendation-demo.tsx`, so deleting `/legacy` would have
left a product whose every card is a TMDB poster with no attribution anywhere.
It now lives in the shell's footer on all five product routes and at the foot of
the signed-out door, held there by `web/e2e/tmdb-attribution.spec.ts` at
390/768/1440 and 320. The retirement PR can delete the legacy copy along with
the rest of the route, and needs to change nothing else about attribution.

One consequence worth naming: the legacy dashboard is currently the only place
in the UI that offers the four demo personas by name. The product selects a
persona by URL (`?userId=` on Discover and Library, `?user=` on Browse, movie
detail, and Quick Picks). A persona switcher in the product shell is recorded
as a follow-up in the
[finish-gate review](records/finish-gate-passes.md#10-re-run-after-cutover-7d), not as
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
