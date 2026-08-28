# Movie detail enrichment — evidence, 2026-08-28

- `before-{1440,390}.png` — `/movies/1` (Toy Story) on the seeded demo stack running `main` at `78e9588`.
- `after-{1440,390}.png` — the same route rendered from branch `feat/movie-detail-enrichment` through the
  `ui-preview` fixture shell (the live API did not yet carry `details`; fixture artwork is local SVG so the
  isolated harness makes no third-party request). In `after-390.png` the fixed bottom navigation is painted
  at its scroll position by the full-page capture and overlaps two buttons — a capture artifact; the 390
  fixture run asserts all three controls and no horizontal overflow.
- `rating-1-idle` → `rating-6-reopened` — the rating interaction frame by frame: idle, hover preview,
  the staggered fill on commit, the pop with its glow, the collapsed "You rated 5/5 · Change rating" chip,
  and the reopened row with Clear rating.

What holds this afterwards: `web/e2e/movie-detail.spec.ts` (24 checks at three viewports, including
"no iframe and no request to a YouTube host before the press"), `rating-stars.test.tsx`, and the
service-backed round trip on Action Fan in `web/tests/e2e/browser-auth.spec.ts`.
