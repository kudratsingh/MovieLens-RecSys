# Ranked rail polish — evidence, 2026-08-28

Before/after captures of Discover's "Next in this ranked set" rail at 1440×900 and 390×844.

- `before-*.png` — the seeded demo stack running `main` at `52ad429`, signed in as the demo actor.
- `after-*.png` — the same route served by the Next dev server from branch `feat/rail-card-polish`
  against the same API. The after shots resolved a different persona than the before shots (the
  dev server's session did not carry the `userId` query), which is why the titles and the policy
  label differ; the subject of these pictures is the card, not the ranking.
- `states-1440.png` — pressed watchlist, recorded watched, undo-after-dismissal and the hover lift
  side by side, produced by applying the state classes in the browser (no writes to the stack).

What to look at: the controls of one-line and two-line titles share one baseline; the "Watched"
label no longer wraps inside its pill; the rank sits in the caption gutter in the display serif
instead of covering the artwork; the poster carries an elevation and an edge highlight; the
caption-plus-controls block is 49 % of the poster's height at 1440 (69 % before).

The gate that holds this afterwards is `web/e2e/discover.spec.ts` (control-baseline equality
across title lengths, the no-wrap guarantee, the density ceiling) and the jest-axe assertions in
the component tests.
