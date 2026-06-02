# ADR 0001 — Frontend Framework

**Status:** Accepted
**Date:** 2026-06-01

## Context

[CLAUDE.md](../../../CLAUDE.md) commits to shipping a Next.js + TypeScript + Tailwind frontend alongside Phase 3 serving. The frontend is not an end-user product — it's a portfolio surface whose job is to make the ML-engineering work visible: feature attribution panels, model/version selection, champion-vs-challenger comparison, and (in Phase 5) a model-health indicator. Catalog search, real auth, and admin dashboards are explicit non-goals; Grafana owns admin views.

The constraints that shape the framework choice are unusual for a typical "pick a frontend stack" decision:

- **Single deployment target.** One demo URL, no SEO requirements, no multi-tenant rendering needs. The framework's deployment-targeting flexibility is irrelevant; what matters is what it gives us *inside* a single app.
- **No real authentication.** Users are impersonated MovieLens user IDs picked from a dropdown. The framework's auth integrations are irrelevant.
- **Poster-heavy UI.** MovieLens 25M has ~62 000 movies. Every surface that lists recommendations, watch history, or comparison views renders dozens to hundreds of poster thumbnails. Image optimization is on the critical path of every page.
- **Server-side secret.** The TMDB API key must stay server-side per the architecture in CLAUDE.md. The frontend host has to be able to act as a backend-for-frontend proxy for TMDB calls, not just a static asset server.
- **Mixed render needs.** The user selector and watch-history view are largely read-only and render cleanly server-side. The champion/challenger view, model-version selector, and feature-attribution panel are interactive and need client hydration. The right framework lets us route each surface to the appropriate render mode without architectural ceremony.
- **A real client against the API, not a hypothetical one.** The CLAUDE.md non-negotiable here is that "designing an API against a real client" is what forces the right backend choices early. The frontend has to be substantive enough to surface those choices — Server Components, real fetch waterfalls, type-checked request/response shapes — not so light that it papers over them.

The framework choice gets pinned in writing before the scaffold lands so the alternatives stay live and reviewable.

## Decisions

### Framework: Next.js 16 (App Router)

The chosen framework is Next.js 16 with the App Router, React 19, and TypeScript 5. Five reasons stack on top of each other:

1. **Server Components map directly onto the rendering needs.** The Phase 3 baseline surface (user selector → top-K poster grid + watch history) is dominated by server-fetched data; rendering it as Server Components means the API response never touches the client bundle, the first paint includes real content, and there's no client-side data-fetching waterfall. The interactive surfaces (model-version selector, champion/challenger toggle, attribution panel hover states) opt into client rendering with `"use client"` at the leaf — small, surgical, no whole-tree hydration. Plain React + Vite would force us to pick one rendering mode globally, then re-introduce SSR or RSC by hand if we ever wanted it.

2. **Route handlers under `app/api/*` give us a server-side place to call TMDB without a second service.** The TMDB API key cannot ship to the browser; that constraint is in CLAUDE.md. Without route handlers we'd need a separate Express or Fastify process *just* to proxy TMDB, with its own deployment, its own TypeScript config, its own logging story. Next.js's route handlers collapse that into the same Node process, the same TS compilation, the same lint config, and the same deployment artifact. The proxy is a ~30-line `app/api/tmdb/[...path]/route.ts` rather than a sibling service.

3. **Next/Image is non-trivial to hand-roll for a poster-heavy UI.** TMDB serves poster images at multiple sizes (`w92`, `w154`, `w185`, `w342`, `w500`, `original`). Hand-rolling a `<picture srcset>` story with the right density-descriptor logic, lazy loading, and blur-up placeholders is a week of fiddly work; Next/Image bundles all of that and, importantly, runs its optimizer on the proxy hop so we can normalize image URLs through the same backend-for-frontend that holds the TMDB key. None of the alternatives below give this for free; all of them would have us reach for a third-party image library and another piece of infra.

4. **App Router's nested layouts match the planned UI shape exactly.** Every recommended surface in CLAUDE.md (Phase 3 baseline, Phase 4 attribution panel, Phase 5 drift indicator, Phase 6 champion/challenger) lives inside a persistent shell — a header with user selector + model-version chip + (eventually) drift status, wrapping per-route content. Nested layouts under `app/(shell)/...` express this without prop-drilling or context plumbing. A non-framework React app would re-render the shell on every route change unless we hand-built a layout-preservation system, which is exactly what App Router is for.

5. **TypeScript integration is first-class and end-to-end.** CLAUDE.md mandates TS strict mode for the frontend. Next.js generates typed route params, typed page props, and a `next-env.d.ts` that hooks up `Image` / `Script` / CSS-module types automatically. Type checking the API boundary (request/response shapes shared between `app/api/*` and client code) is straightforward because both sides compile under the same tsconfig. With a separate Express proxy we'd be maintaining two TS projects and a shared types package.

### Styling: Tailwind CSS v4

The dense data-visible panels — feature attribution rows, side-by-side comparison cells, poster grids with hover metadata — are utility-class territory. Hand-rolling CSS modules for them adds class-name plumbing without buying anything: the styles are local to the component, they're terse, and they change often during prototyping. Tailwind absorbs that without an indirection layer.

Tailwind v4 specifically (not v3) is chosen because (a) it ships with `@import "tailwindcss"` and CSS-first theme variables, eliminating the need for a `tailwind.config.js` for the baseline; (b) its compile times are materially faster in Turbopack, which matters when `next dev` is doing both Next.js compilation and Tailwind compilation on every save; and (c) its CSS-variable-first design lets us drop a theme token system later (e.g. one for the champion column, one for the challenger column in Phase 6's comparison view) without a config rewrite.

### Linting: ESLint 9 (flat config)

`eslint-config-next` for React/Next-aware rules, run via `npm run lint` (which invokes the flat-config-aware `eslint` binary directly, not `next lint` — Next 16 deprecates the `next lint` wrapper in favor of calling ESLint directly with the bundled config). Prettier is intentionally deferred to a follow-up PR alongside CI integration to keep this PR focused on the scaffold itself; the rationale for *that* split is captured in the PR description, not here.

### Dev port: 3001 (not the Next.js default 3000)

`docker-compose.yml` exposes Grafana on host port 3000. `next dev` defaults to 3000 and would collide silently — the dev server would either fail to bind or, worse, work by happenstance because Grafana isn't running locally during the session, and then break the moment someone starts the full compose stack. Both `dev` and `start` scripts in `web/package.json` pin port 3001 so the foot-gun never reaches a developer.

The choice of 3001 specifically (not 3002, 5173, etc.) is conventional — `+1` from the default is the well-trodden path and signals to a future reader that the choice was made to avoid a conflict, not by preference.

## Alternatives considered

- **Streamlit.** The fastest path to a demo and the most common choice for ML-project frontends. Rejected on two grounds. First, it reads as a data-science prototype rather than engineering work — and "reads as engineering work" is one of the things the portfolio surface has to do, per CLAUDE.md. Second, the technical fit is genuinely bad for the planned surfaces: no real component model means the champion/challenger view (two synchronized columns with diff highlighting) is awkward; routing is single-page-by-default so the per-route nesting can't be expressed; and there's no path to a client-impersonation pattern (Streamlit's session state is server-bound). Streamlit exercises essentially none of the muscles this project is built to teach.

- **Plain React + Vite.** The lightest viable option. Rejected because adopting it commits us to rebuilding by hand most of what Next.js bundles: `react-router` for routing, a separate Express or Fastify process for the TMDB proxy, manual `<picture srcset>` work or a third-party image library for poster optimization, and either no SSR or a custom SSR setup if we ever want server-rendered first paints on the Phase 3 surfaces. The line of integration code we'd write to glue these together is exactly the line of code Next.js absorbs. The "no opinionated framework" advantage Vite usually offers is wasted here — we *want* the opinions because the alternative is reinventing them.

- **Remix (now React Router v7).** The closest competitor and the only alternative that scored highly across every constraint. Strong nested-layout story, Server Components story now via the React Router v7 / Remix v3 convergence, first-class TypeScript, comfortable form-and-loader pattern. Tie-broken in favor of Next.js on two pragmatic grounds: (a) ecosystem inertia — `app/api/*` route handlers, `next/image`, and middleware are the patterns an ML-engineering interviewer is most likely to recognize and ask about, which directly serves the project's portfolio purpose; and (b) deployment story flexibility — Next.js runs on any Node host or Vercel without surgery, and the option of "stand up a Vercel preview URL on the demo branch" is one fewer thing to figure out if the project ever needs a live shareable instance. A reasonable alternative-universe version of this project ships on Remix and is no worse.

- **SvelteKit.** Excellent framework, technically excellent fit (image optimization, nested layouts, server-side endpoints, end-to-end TypeScript). Rejected because the project's wider stack is React-shaped — CLAUDE.md commits to React + Tailwind + TypeScript, and the surrounding libraries (Recharts or visx for the eventual model-health/drift visualizations, react-query if we reach for client-side cache management) are React-ecosystem. Adopting Svelte adds a learning detour with no portfolio payoff; the lesson here is the ML engineering around the framework, not the framework itself.

- **HTML + htmx (no SPA).** Considered briefly. Genuinely interesting for a small portfolio app and would force minimal JS. Rejected because two of the planned surfaces — Phase 6's champion/challenger comparison with interactive diff highlighting, and the model-version selector that re-renders attribution data against a live API — are exactly the interactive patterns htmx makes awkward. htmx is the right tool for forms-and-fragments apps; the frontend here is closer to a small interactive dashboard, and the wrong tool for that.

- **Astro with React islands.** Considered. Strong static-first story and lightweight client bundle. Rejected because the planned surfaces are *not* static-first — every poster grid, attribution panel, and comparison view is per-user-impersonation-and-per-model-version data, which means SSR (or RSC) on every request, not build-time generation. Astro can do dynamic SSR but it's not what the framework optimizes for; using it would mean opting out of its core advantage on every page.

## Consequences

- **Code layout.** The frontend lives at `web/` as a sibling of `src/` and `pipelines/`, with its own `package.json`, `tsconfig.json`, `eslint.config.mjs`, and `node_modules`. Frontend tooling is isolated from Python tooling — no `pyproject.toml` interaction, no shared lint config, no shared CI step beyond the orchestration in `.github/workflows/ci.yml`. The agreed convention is that frontend branches use the `feat/frontend-*` namespace and frontend ADRs live under `docs/adr/frontend/` (per the per-team ADR namespace policy adopted 2026-06-01).

- **Next.js 16 is a breaking-change release relative to most Next.js documentation indexed in third-party sources.** The canonical reference for route handlers, layouts, server actions, and middleware is `web/node_modules/next/dist/docs/` — not external Stack Overflow answers, not training-data patterns from earlier Next versions, not the public Next.js docs site if it lags. `web/AGENTS.md` codifies this for any agent (Claude or otherwise) touching the frontend.

- **CI integration follows in a separate PR.** Adding the `web` job to `.github/workflows/ci.yml` (typecheck + lint + build, conditional on changes under `web/`), the Makefile targets (`web-dev`, `web-build`, `web-lint`, `web-typecheck`), and Prettier config is bundled as the immediate next PR. Keeping it separate from the scaffold PR keeps each change reviewable on its own concern — "is this scaffold the right scaffold?" vs "is this CI wiring the right CI wiring?".

- **TMDB proxy lives under `app/api/tmdb/`.** When the proxy lands (Phase 3, alongside the user selector and poster grid), the TMDB API key reads from an env var (`TMDB_API_KEY`), the route handler attaches it to outgoing requests, and the browser only ever sees Next.js-internal URLs. `next/image` is configured with TMDB's image host in `next.config.ts`'s `images.remotePatterns` so the optimizer can fetch the upstream image directly while keeping the API key path strictly server-side.

- **API contract is intentionally out of scope here.** The contract between the Next.js app and the FastAPI service gets its own ADR when Phase 3 serving begins, and per the per-team ADR namespace policy, that ADR is *cross-cutting* and lives at the top level (`docs/adr/`) rather than under `docs/adr/frontend/`. Until then, the frontend operates against mocked responses inside `app/api/*` route handlers — fixtures that match the eventual real shape closely enough to let UI work proceed.

- **Build/runtime environment.** Node 20+ is required (Next 16 minimum). Local dev is `npm run dev` on port 3001. Production build is `next build` + `next start`; static export (`output: "export"` in `next.config.ts`) is not viable because the TMDB proxy and any future RSC-fetching route requires a Node runtime. If the project ever needs a deployable demo URL, the deployment target is a Node host (Vercel, Fly.io, or `docker-compose` extension) rather than an S3/CloudFront static bucket.

- **State management is deferred.** No client-side data-fetching library (TanStack Query, SWR, Redux, Zustand) is added in the scaffold. Server Components + Server Actions + `fetch` cover the Phase 3 baseline surfaces. The decision of whether to add TanStack Query for the Phase 6 champion/challenger view (which has the strongest client-side cache argument — two parallel rec lists against the same impersonated user) gets its own ADR when that surface is built.

- **Authentication / impersonation flow is deferred.** The MovieLens user-ID impersonation pattern (cookie vs URL param vs Server Action with form state) is not decided in this ADR. It earns its own frontend ADR when the user selector lands.

- **Styling token system is deferred.** Tailwind v4's CSS-variable-first theming makes it natural to introduce a token layer later (one for the champion column, one for the challenger column in Phase 6; one for "ok / degraded" states in Phase 5's drift indicator). The decision of whether to formalize that as a theme file vs inline CSS custom properties is its own (small) ADR when the second consumer of a token shows up — premature now.

- **Frontend testing strategy is deferred.** No Vitest, Playwright, or React Testing Library setup in the scaffold. The minimum viable check in CI is `tsc --noEmit` + ESLint + `next build`; a real test strategy (component tests with RTL, e2e with Playwright against a running stack) lands when there's an actual surface to test, with its own ADR.
