# The Seen experience — evidence, 2026-08-28

Branch `feat/seen-history`, captured after the backend merge and the type
reconciliation (`a3217bf`). Every frame is the **live seeded Compose stack** —
real Keycloak with `DEV_AUTH_BYPASS=false`, real FastAPI, real RLS, the API and
web images both rebuilt from this worktree — signed in as the `demo` actor and
exploring as **Action Fan (900000101)**, the persona that owns Library's
movie-state and rating writes. Cold Start (900000104) is not touched by anything
here; it read `0/0/0` before and after.

Captured against `http://localhost:3001`, full-page, on Desktop Chrome, by a
one-off Playwright spec that **was not committed** — it lived under a scratch
directory outside the repository. To re-shoot this set, drive `/library?tab=history`
on a seeded stack signed in as Action Fan at 1440/768/390 and reproduce the
filenames listed below; every frame here is reachable through the product with
no fixtures. A committed, re-runnable capture script for the whole product is
being added with the `current/` matrix — see the
[evidence index](../README.md#re-capturing).

| Image | Viewport | What it shows |
|---|---|---|
| `seen-default-1440.png` | 1440×900 | The tab at rest: label **Seen** over the unchanged `history` URL value, the spotlight on *Fight Club* with backdrop, runtime, TMDB score, cast and `Seen on Nov 14, 2023`, the shared rating chip, `Remove from history`, `1 of 8`, and the rows carrying their `TMDB 8.4` marks |
| `seen-default-768.png` | 768×1024 | The same at the tablet width |
| `seen-default-390.png` | 390×844 | The same on the pinned mobile profile |
| `seen-spotlight-next-1440.png` | 1440×900 | After `Next` — readout `2 of 8`, a different title, the row list unmoved beneath it |
| `seen-sort-tmdb-1440.png` | 1440×900 | `Highest TMDB score`, the ordering the row marks let a reader check by eye |
| `seen-sort-rating-1440.png` | 1440×900 | `Highest rated`, your own star value |
| `seen-filtered-1440.png` | 1440×900 | Genre `Drama` with the 1990–1999 bounds; the readout counts the **query**, not the loaded window |
| `seen-search-1440.png` | 1440×900 | `  The   Matrix ` typed with the whitespace it was typed with, echoed back collapsed as `The Matrix` |
| `seen-filtered-empty-1440.png` | 1440×900 | A range matching nothing: `No matches in this collection` with `Clear filters`, the spotlight correctly absent, and the tab badge still reading the unfiltered 8 |
| `seen-rating-open-1440.png` | 1440×900 | The spotlight's rating editor open on a committed value |
| `seen-rating-committed-1440.png` | 1440×900 | After commit — the collapsed `You rated N/5 · Change rating` chip, which appears only once the API has answered |
| `seen-remove-confirm-1440.png` | 1440×900 | The confirmation: the consequence sentence, the destructive `Remove from history`, and `Keep it` |
| `seen-removed-1440.png` | 1440×900 | After confirming — the spotlight has advanced past the title that left |
| `seen-rated-tab-1440.png` | 1440×900 | Rated, unchanged: no spotlight, no genre control, and the three orderings it already had |
| `seen-watchlist-tab-1440.png` | 1440×900 | Watchlist, unchanged |

## Reading these

Two capture artifacts, both the harness rather than the product. The sticky
product header and the `Skip to content` link are painted at their scroll
position by a full-page screenshot, so on the taller frames they appear partway
down the page; and at 390 the fixed bottom navigation lands mid-frame for the
same reason. The responsive matrix in `web/e2e/library-slice.spec.ts` and the
320px sweep in `web/e2e/finish-gate.spec.ts` are what actually assert layout.

## The two writes, and why removal is not on a seeded row

The walk writes twice and reverts both: a star value on the spotlight's current
title, put back through the BFF at its exact stored value (a seeded `4.5` is a
half-star the whole-star control cannot express, so it is restored by request
rather than by hand); and `watched` on **2001: A Space Odyssey (924)**, a title
Action Fan has never seen, which is the subject of the removal frames and is
removed again.

Removal deliberately never lands on a seeded row. `PUT .../watched` carries no
body, so it stamps `watched_at = now()` — a seeded 2023 date is not restorable
through the API, and removing one to photograph the confirmation would have cost
the fixture something no later step could give back. Afterwards Action Fan reads
8 rated / 0 watchlist / 8 history with all eight 2023 dates and ratings intact,
and 924 is back to a fully-null state row that appears in no tab and is in no
exclusion set.

## What holds this afterwards

`web/tests/e2e/seen-journey.spec.ts` (service-backed, the spotlight rating round
trip on the real stack), the Seen cases in `web/e2e/library-slice.spec.ts` and
the two finish-gate matrix states, `web/tests/unit/library-spotlight.test.ts`
and `library-url-state.test.ts`, and the `library-seen` route in
`web/tests/perf/browser-timing.spec.ts` — LCP 92 ms, CLS 0.0000, and the
structural proof that the spotlight's backdrop is out of flow, so a late image
cannot push the rows down even on a cache hit.
