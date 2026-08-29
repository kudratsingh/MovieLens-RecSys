# Frontend evidence index

Fourteen capture sets: one that describes the product as it stands, and thirteen
that were committed with the work they document. This page says what each one
shows, when it was taken, and — the part that decides whether a screenshot means
anything — **how** it was captured.

## Two kinds of provenance, and why the distinction is kept

- **Service-backed** — the seeded Compose stack producing the frame on its own,
  with `DEV_AUTH_BYPASS=false`: real Keycloak, real FastAPI, real RLS, the local
  catalog snapshot, the feature and model servers, and the web BFF. This is
  evidence about the system.
- **Fixture-mode** — the shipped components mounted at `/ui-preview/*` against
  recorded data through `web/lib/resources/fixture-gate.ts`, which throws outside
  fixture mode. This is how the matrix reaches states a healthy stack cannot be
  asked to hold still in: a load in flight, an empty ranked set, a dead upstream
  read, a failed poster.

Neither substitutes for the other, and every set says which it used. A
fixture-mode frame is a claim about a component; a service-backed frame is a
claim about the product.

## Which sets describe the current build

**[`current/`](current/README.md) is the complete picture of the product**, and
it is the one set to link from anywhere that wants to show what this looks like
today. Its directory name is stable and the capture overwrites it in place, so a
link to `current/discover-desktop-1440.png` keeps meaning the current build
after the next re-shoot — which no dated folder can promise.

Everything else on this page is history. Each dated set remains the record of
what a particular bundle or round changed, and is worth reading for that; none
of them is a claim about the product as it stands. In particular
`sweep-2026-08-27`, which held this role until 2026-08-29, predates PRs #77,
#78, #79, #81, #84 and #85 — the re-laid-out ranked rail, the rating prompt's
ending, the movie page's TMDB details and trailer, the featured slot's Skip and
preference, the Library's History tab becoming Seen, and Browse's loading
reservation. Read it as the "before" for those, not as the product.

The three per-surface sets from 2026-08-28 keep the same standing: they are
before/after evidence for one surface each, and `current/` supersedes their
"after" halves.

| Surface | Where the change was argued | Where the surface is now |
|---|---|---|
| Discover's ranked rail | `rail-polish-2026-08-28` | `current/discover-*` |
| Movie detail | `movie-detail-2026-08-28` | `current/movie-detail-*` |
| Library's Seen tab | `seen-2026-08-28` | `current/library-seen-*` |

## The sets

| Set | Date | Build | Covers | Provenance |
|---|---|---|---|---|
| [`current/`](current/README.md) | Re-shot in place | Whatever `main` was at the last re-shoot; the set's own README names the commit | **The product as it stands**: Discover, Browse, movie detail, Library, Seen, Quick Picks at three viewports, plus the signed-out door and the prediction-audit disclosure. 21 frames | Service-backed, read-only, `npm run evidence:current` |
| [`baseline/`](baseline/) | 2026-08-21 | `c73b967` (pre-redesign) | The Phase 3 dashboard at three viewports plus API-error and poster-fallback states. 9 frames | Service-backed |
| [`bundle-4/`](bundle-4/README.md) | 2026-08-21 | Bundle 4 (PR #52) | Route shells and the visual system before any live route consumed it. 8 frames | Fixture-mode |
| [`bundle-5b/`](bundle-5b/README.md) | 2026-08-21 | Bundle 5B (PR #56) | The full `/discover` state matrix — every policy, disclosure, empty, loading and failure state. 33 frames | Fixture-mode (recorded scenarios through the live route) |
| [`bundle-5c/`](bundle-5c/README.md) | 2026-08-21 | Bundle 5C (PR #57) | Browse and movie detail on the catalog contract: filters, sorts, cursor continuation, restoration. 16 frames | Fixture-mode (recorded catalog endpoint) |
| [`bundle-5d/`](bundle-5d/README.md) | 2026-08-21 | Bundle 5D (PR #55) | Library tabs, rows, ratings summary, empty and dead-read states, and the removal confirmation. 11 frames | Fixture-mode (recorded Library client) |
| [`bundle-6/`](bundle-6/README.md) | 2026-08-21 | Bundle 6 (PR #58) | The Quick Picks deck against the recorded recommendation contract. 8 frames | Fixture-mode |
| [`bundle-7a/`](bundle-7a/README.md) | 2026-08-21 | 7A finish gate (PR #63) | The named finish-gate state matrix at 390/768/1440. 36 frames — the largest set, and the one the gate cites | **Both**, labelled per file |
| [`bundle-7c/`](bundle-7c/README.md) | 2026-08-21 | Bundle 7C (PR #61) | Only the surfaces the control-convergence refactor re-renders: movie detail, Library, Quick Picks. 8 frames | Fixture-mode |
| [`bundle-7d/`](bundle-7d/README.md) | 2026-08-21 | 7D cutover (PR #65) | Only what the cutover moved: the front door signed in and out, `/legacy` labelled, Browse/detail/Library on the shared shell. 10 frames | Service-backed |
| [`sweep-2026-08-27/`](sweep-2026-08-27/README.md) | 2026-08-27 | `feat/frontend-sweep` on `099ac3d` | **Complete**: five product routes × three viewports, after the sweep changed the write path, posters, catalog ordering, shell and two read models. 15 frames | Service-backed, read-only (presses no decision control, to keep Cold Start clean) |
| [`rail-polish-2026-08-28/`](rail-polish-2026-08-28/README.md) | 2026-08-28 | `feat/rail-card-polish` vs `52ad429` | Before/after of Discover's ranked rail at 1440 and 390, plus a control-states frame. 5 frames | Service-backed before; branch dev server after (the README records that the two resolved different personas, and why that does not matter here) |
| [`movie-detail-2026-08-28/`](movie-detail-2026-08-28/README.md) | 2026-08-28 | `feat/movie-detail-enrichment` vs `78e9588` | Before/after of the movie page, plus the rating interaction frame by frame. 10 frames | Service-backed before; fixture-mode after (the live API did not yet carry `details`) |
| [`seen-2026-08-28/`](seen-2026-08-28/README.md) | 2026-08-28 | `feat/seen-history` at `a3217bf` | The Seen experience at 1440/768/390: spotlight, search, filters, rankings, re-rate, confirmed removal. 15 frames | Service-backed, Action Fan; Cold Start read `0/0/0` before and after |

## Re-capturing

`npm run evidence:current` re-shoots the current matrix in place, and is the one
to run after any change that alters what a product route looks like:

```bash
make demo-up && make demo-seed && make demo-smoke
cd web && MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:current
```

It rewrites `current/README.md` from what it observed — the commit, the serving
policy each persona was served, the catalog's poster coverage, the enrichment
behind the movie page — so the provenance cannot drift from the pictures. It
also refuses to ship a frame containing an image that failed to decode, and
re-encodes any capture over 700 KB.

Each historical set has its own `npm run evidence:*` script in
`web/package.json` (`evidence:bundle4` … `evidence:bundle7d`,
`evidence:sweep`), and `evidence:baseline` captures the pre-redesign matrix. The
fixture-mode scripts need the app running with `MOVIELENS_UI_FIXTURE_MODE=1`;
the service-backed ones need `make demo-up && make demo-seed` and read
`MOVIELENS_DEMO_URL`.

The three 2026-08-28 sets were captured by one-off specs that were not committed
with them — `seen-2026-08-28/README.md` names a path under `scratchpad/` that is
not in the repository, and the other two were driven by hand. To re-shoot any of
them today, drive the same routes with a short Playwright spec against a seeded
stack, at the viewports and filenames the set's README lists; the surfaces are
all reachable without fixtures. In practice there is rarely a reason to: they
are before/after arguments for changes that have landed, and
`evidence:current` is what keeps a picture of the result honest.
