# Bundle 7C control-convergence evidence

PR 7c is a behaviour-preserving refactor, so only the surfaces it actually
re-renders are captured here. Browse, the poster grid, and every Discover state
that does not involve a control are unchanged; their Bundle 5 captures still
stand.

What is new is that movie detail and the Library now show the *same*
confirmation, the same destructive treatment, and the same star editor, because
they render the same component with different control sets — and that Quick
Picks has a deliberate entry point from Discover rather than a fourth navigation
slot.

Capture command (Next.js running in fixture mode):

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3110
# In a second terminal:
MOVIELENS_UI_PORT=3110 npm run evidence:bundle7c
```

## Matrix

| Capture | Width | What it shows |
|---|---|---|
| `discover-quick-picks-entry-desktop` | 1440 | The ranked set followed by the labelled Quick Picks entry and its one-line reason |
| `discover-quick-picks-entry-mobile` | 390 | The same entry on the mobile profile, below the rail and above watch history, with the shell still at three primary routes |
| `detail-state-controls-desktop` | 1440 | The converged control row: `Watchlist` leading, the destructive `Watched · remove`, `Not for me`, and the rating panel |
| `detail-state-controls-mobile` | 390 | The same row on the narrow profile |
| `detail-remove-confirm-desktop` | 1440 | Removing watched history on detail, in the confirmation shape the Library already used |
| `library-remove-confirm-desktop` | 1440 | The same confirmation on a Library History row, with `Remove rating` still visibly a different action |
| `library-rated-mobile` | 390 | A Rated row's half-star editor and 44px targets, unchanged by the move onto the shared family |

Automated coverage for these states — the confirm/cancel focus path, the
`aria-disabled` in-flight semantics, the Quick Picks entry href and touch
target, horizontal overflow, and zero critical or serious axe violations — lives
in `web/components/movie/movie-state-controls.test.tsx`,
`web/components/movie/movie-detail-view.test.tsx`,
`web/components/library/library-experience.test.tsx`, and
`web/e2e/discover.spec.ts`.

Service-backed proof that the write path still commits, reconciles, and survives
a round trip is unchanged: `web/tests/e2e/browser-auth.spec.ts` and
`web/tests/e2e/discover-journey.spec.ts` run in the `browser-auth-e2e` CI job
against the seeded Compose stack with `DEV_AUTH_BYPASS=false`.
