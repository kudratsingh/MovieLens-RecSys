# Frontend sweep evidence — 2026-08-27

The five product routes at the three contracted viewports, recaptured after the
sweep.

Unlike the bundle folders, this one is **complete rather than differential**.
The sweep changed the write path, the poster pipeline, the catalog's default
ordering, the shell, and two read models, so no earlier matrix still describes
any of these routes accurately. The bundle folders remain the record of what
each bundle changed; this is the record of what the product looks like now.

## Provenance

| | |
|---|---|
| Branch | `feat/frontend-sweep` |
| Base commit | `099ac3d` (`main` at `9fd6b2b` plus the poster backfill) |
| Captured | 2026-08-27, after the full WI-1…WI-6 tree was in the worktree |
| Stack | `docker-compose.yml` + `docker-compose.demo.yml`, project `movielens-demo`, images **rebuilt from this branch** (`make demo-up`) and re-seeded (`make demo-seed`) |
| Auth | Real Keycloak, `DEV_AUTH_BYPASS=false`, PKCE through the Next BFF, `demo`/`demo` |
| Catalog | 120 rows, **120/120 posters, 120/120 overviews** in `movie_catalog_metadata`; every stored URL verified live by `make catalog-verify` (checked 120, failures 0) |
| Warm serving policy at capture time | `item-item-cosine+lightgbm`, `learned: true`, 8 positive seeds, `filter_policy: watched-and-dismissed-excluded-v1` — printed by the capture run rather than assumed |
| Browse poster coverage at capture time | 48/48 on the first two windows — printed by the same run |

## Capture command

```bash
make demo-up          # rebuild api + web from this branch
make demo-seed        # re-seed the reviewed 120-title fixture
cd web
MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:sweep
```

`web/scripts/capture-sweep-evidence.mjs` signs in through Keycloak, records the
warm persona's serving policy and the catalog's poster coverage, then captures
each route. It **only reads**: no capture presses a decision control, so Quick
Picks is photographed as Cold Start without spending one of that persona's
signals. Persona assignment follows the ownership table in
`web/tests/e2e/browser-auth.spec.ts` — Discover as Drama Fan, Browse as Eclectic
Viewer, Library and movie detail as Action Fan, Quick Picks as Cold Start.

## Matrix

Each row is three files: `-mobile-390`, `-tablet-768`, `-desktop-1440`.

| Surface | Files | What the sweep changed in it |
|---|---|---|
| Discover | `discover-*` | The featured slot is a queue position 24 deep (F1); the poster is real artwork rather than a fallback mark (S5, S6, F2); the title prints its year once, on the metadata line (F4); the policy label follows the reported `serving_policy.learned`; the persona and the actor are both named in the header at every width (S9) |
| Browse | `browse-*` | Opens on **Most watched here** rather than alphabetically (F3) — the first window is recognisable titles with artwork instead of `2001`, `Ace Ventura`, `Aladdin`; cards carry the shared control family, so `Watched` is offered beside `Watchlist` (P2-9); `Partial details` no longer appears, because every row now has an overview (F2) |
| Movie detail | `movie-detail-*` | One display title above one metadata line (F4, P2-1); the poster falls back to the shared mark rather than a broken image when artwork is missing (S2, F4) |
| Library | `library-*` | Rows carry artwork and print the year on the metadata line (F5) — the payload had neither field before this sweep; the fallback mark is the shared one at row density (F4) |
| Quick Picks | `quick-picks-*` | Runs **inside the product shell** for the first time (S4): one `<main>`, a skip link, both navigations, a sign-out, and a persona resolved server-side to `Cold Start` instead of the `Demo persona 900000104` placeholder; the poster degrades to the shared mark instead of a broken image (S3) |

## What these pictures do not settle

- **N4 — Library spends the first mobile viewport on identity copy.** Still true
  in `library-mobile-390.png`: the tabs sit below the fold-and-a-half and the
  first rated row is at the very bottom. S9 delivered the precondition (the
  persona is named in the header now), and the layout change itself is deferred
  as U13.

Two things an earlier capture of this matrix showed are gone from this one,
because they were fixed on the branch before it opened as a PR: the persona
stated twice (the shell is now the only place that names it — the Quick Picks
deck's repeated line and Library's page eyebrow were removed), and `Not for me`
sitting 43px to the right of the two buttons above it on Discover at 390. The
Discover history region is also no longer text-only: each row is a link with a
thumbnail. The pictures were recaptured after those changes.

## What guards these surfaces afterwards

The pictures are evidence, not the gate. What actually holds:

- `web/e2e/finish-gate.spec.ts` — the named state matrix at 390/768/1440 plus a
  320px sweep, with axe on every state.
- `web/e2e/{discover,browse-detail,library-slice,quick-picks,poster-fallback,resource-states,route-shells,shell-identity}.spec.ts`
  — fixture-mode rendering and layout at the same three viewports.
- `web/tests/e2e/` — the serialized service-backed journeys, including
  `shell-and-doors.spec.ts` for the landmarks each of these routes carries and
  `persona-hygiene.spec.ts` for the state they are left in.
- `web/tests/perf/browser-timing.spec.ts` — LCP, CLS, and the structural
  promises (reserved poster boxes, no per-card TMDB fan-out, bounded pages) on
  the pinned mobile profile.
- `make catalog-verify` — every stored poster URL still resolves. Run by hand or
  nightly; deliberately not a CI gate on a third party's CDN.
