# Current build evidence

The product as it stands on `main`, at the three contracted viewports, plus the
signed-out door and the model-evidence disclosure.

This directory has a **stable name and is overwritten in place** on every
re-shoot, so a document can link `current/discover-desktop-1440.png` and keep
meaning the current build. The dated folders beside it stay as they are: each one
is the record of what a particular bundle changed, and none of them is a picture
of the whole product. This one is.

## Provenance

| | |
|---|---|
| Captured | 2026-08-29 |
| Commit | `b92e15049e600c0d15be60ce6fdc449fc107b07a`, with uncommitted changes under `web/`, `src/`, `synthetic/` or `infra/` — the images were built from that tree rather than from the commit alone |
| Stack | `docker-compose.yml` + `docker-compose.demo.yml`, project `movielens-demo`, images built from this tree (`make demo-up`) and seeded (`make demo-seed`) |
| Smoke | `make demo-smoke` passed before the first capture |
| Auth | Real Keycloak, `DEV_AUTH_BYPASS=false`, authorization code + PKCE through the Next BFF, `demo`/`demo` |
| Viewports | 390×844, 768×1024, 1440×1000 · `colorScheme: dark` · device scale factor 1 · `prefers-reduced-motion: reduce` |
| Catalog | 120 rows over 3 cursor pages: **120/120 posters**, **120/120 overviews** |
| Images | Every capture waited for every in-viewport `<img>` to finish decoding; a broken frame fails the run rather than shipping. The rails and grids below the fold stay lazy, which is the behaviour under test elsewhere |

Reduced motion is emulated because the product honours it by shortening
transitions and nothing else — `globals.css` sets durations to `0.01ms` and
leaves every layout rule alone — so it removes capture flake without changing
what is being photographed.

### Serving policy each persona was actually served

Printed by the capture run from a live `GET /users/{id}/recommendations`, not
asserted from the design contract.

| Persona | `serving_policy` |
|---|---|
| Action Fan (900000101) | `item-item-cosine+lightgbm`, `learned: true`, 8 positive signals, reason `learned-two-stage: item-item-cosine retrieval over 8 positive seeds, ranked by demo-lgbm-v1`, filter `watched-and-dismissed-excluded-v1` |
| Drama Fan (900000102) | `item-item-cosine+lightgbm`, `learned: true`, 8 positive signals, reason `learned-two-stage: item-item-cosine retrieval over 8 positive seeds, ranked by demo-lgbm-v1`, filter `watched-and-dismissed-excluded-v1` |
| Eclectic Viewer (900000103) | `item-item-cosine+lightgbm`, `learned: true`, 11 positive signals, reason `learned-two-stage: item-item-cosine retrieval over 11 positive seeds, ranked by demo-lgbm-v1`, filter `watched-and-dismissed-excluded-v1` |
| Cold Start (900000104) | `popularity`, `learned: false`, 0 positive signals, reason `cold-start: 0 positive watched signals below threshold 5`, filter `watched-and-dismissed-excluded-v1` |

Cold Start reporting a fallback policy is the correct answer, not a defect: the
persona is seeded empty and the threshold is five positive signals.

### The movie page's subject

`The Matrix (1999)`, movie 2571, chosen because it is in Action Fan's
seeded history *and* carries enrichment — a title without `details` would
photograph the degraded page and pass it off as the product. What the API
returned for it at capture time:

- poster: present
- backdrop: present
- tagline: present
- runtime: 136 min
- TMDB score: 8.3/10 from 28573 ratings
- cast: 6 named
- trailer: present

## Capture command

```bash
make demo-up          # build api + web from this commit
make demo-seed        # seed the reviewed 120-title fixture
make demo-smoke       # must pass: warm personas have to report learned: true
cd web
MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:current
```

`web/scripts/capture-current-evidence.mjs` signs in through Keycloak, records the
policies and coverage above, captures each surface, and rewrites this file from
what it observed. It **only reads**: no capture presses a decision control, so
Quick Picks is photographed as Cold Start without spending one of that persona's
signals. Persona assignment follows the ownership table in
`web/tests/e2e/browser-auth.spec.ts`.

## The matrix

| Surface | Persona | Widths | What it shows |
|---|---|---|---|
| `sign-in-door-*` | signed out | `mobile-390`, `desktop-1440` | The only unauthenticated screen in the product. |
| `discover-*` | Drama Fan (900000102) | `mobile-390`, `tablet-768`, `desktop-1440` | The front door for a signed-in viewer: the featured queue position, the ranked rail, and the watch history beside them. |
| `discover-why-this-*` | Drama Fan (900000102) | `desktop-1440` | Both disclosure steps open, scrolled to the second: the recorded prediction audit — policy, model and feature versions, feature event time, input-state revision and hash, request id, per-stage latency — and the feature values behind the rank score. |
| `browse-*` | Eclectic Viewer (900000103) | `mobile-390`, `tablet-768`, `desktop-1440` | The catalog on its default Most watched here ordering, with the shared control family on every card. |
| `movie-detail-*` | Action Fan (900000101) | `mobile-390`, `tablet-768`, `desktop-1440` | An enriched title: backdrop, tagline, runtime, crowd score, cast, and the shared rating control. |
| `library-*` | Action Fan (900000101) | `mobile-390`, `tablet-768`, `desktop-1440` | The Rated collection, which is the Library's default tab. |
| `library-seen-*` | Action Fan (900000101) | `mobile-390`, `tablet-768`, `desktop-1440` | The Seen tab: search, genre and year filters, five rankings, and the spotlight walking the filtered list above it. |
| `quick-picks-*` | Cold Start (900000104) | `mobile-390`, `tablet-768`, `desktop-1440` | One decision at a time, photographed at zero signals. No control on this page is pressed. |

## Files

21 PNGs, 4802 KB total.

| File | Size |
|---|---|
| `sign-in-door-mobile-390.png` | 43 KB |
| `sign-in-door-desktop-1440.png` | 61 KB |
| `discover-mobile-390.png` | 81 KB |
| `discover-tablet-768.png` | 371 KB |
| `discover-desktop-1440.png` | 245 KB |
| `discover-why-this-desktop-1440.png` | 248 KB |
| `browse-mobile-390.png` | 103 KB |
| `browse-tablet-768.png` | 376 KB |
| `browse-desktop-1440.png` | 624 KB |
| `movie-detail-mobile-390.png` | 204 KB |
| `movie-detail-tablet-768.png` | 504 KB |
| `movie-detail-desktop-1440.png` | 266 KB |
| `library-mobile-390.png` | 72 KB |
| `library-tablet-768.png` | 163 KB |
| `library-desktop-1440.png` | 148 KB |
| `library-seen-mobile-390.png` | 73 KB |
| `library-seen-tablet-768.png` | 326 KB |
| `library-seen-desktop-1440.png` | 374 KB |
| `quick-picks-mobile-390.png` | 80 KB |
| `quick-picks-tablet-768.png` | 204 KB |
| `quick-picks-desktop-1440.png` | 238 KB |

## A defect these pictures record

The Seen tab's filter row does not fit at the two narrower viewports, and the
captures show it rather than hide it. Measured by this run, on the same page
load that was photographed:

| Viewport | Form width | Content width | Search field | Button overhang |
|---|---|---|---|---|
| `mobile-390` | 358 px | 358 px | 34 px | none |
| `tablet-768` | 304 px | 401 px | 34 px | 97 px past the form |
| `desktop-1440` | 512 px | 512 px | 145 px | none |

Where the content is wider than the form, the `Filter` button is painted
outside its own form and lands on the `Genre` control beside it; where the
search field is a few dozen pixels wide, it is a text input nobody can read what
they typed into. The Rated and Watchlist tabs are unaffected — they carry the
same form without the year bounds, which is the part that will not shrink.

This is a product defect on `main`, not a capture artifact: it reproduces on
reload, at every width below the point where `.library-filter` reaches its
`max-width`, and the same row is correct at `desktop-1440`. It belongs to the
Seen work rather than to this evidence set, so it is recorded here and fixed
elsewhere. These four numbers are re-measured on every re-shoot, so this section
goes away on its own when the layout is fixed.

## What these pictures are not

They are evidence, not the gate. A screenshot proves a surface rendered once on
one stack; what actually holds these surfaces is
`web/e2e/finish-gate.spec.ts` (the named state matrix with axe at 390/768/1440
plus a 320px sweep), the fixture-mode route specs beside it, the serialized
service-backed journeys in `web/tests/e2e/`,
`web/tests/perf/browser-timing.spec.ts` for LCP, CLS and the structural
promises, and `make catalog-verify` for the stored poster URLs.

Nor are they a substitute for the moderated sessions
`docs/frontend/finish-gate-review.md` is still holding on. Pictures show what
the product looks like; they say nothing about whether anyone can use it.
