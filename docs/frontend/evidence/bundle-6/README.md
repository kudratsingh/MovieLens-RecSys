# Bundle 6 Quick Picks evidence

These captures mount the production Quick Picks deck against the recorded
recommendation contract in `web/lib/quick-picks/fixtures.ts`. The isolated
preview route renders the same component, machine, validators, and policy
branching as `/quick-picks`; only the durable boundary is simulated, so a
screenshot here is evidence about the shipped component rather than a mock of
it. Fixtures reach the page through the 5A fixture gate, which throws outside
the explicit fixture mode.

Capture command (with the app running on the isolated preview port):

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3104
# In a second terminal:
npm run evidence:bundle6
```

| Capture | Viewport | What it shows |
|---|---|---|
| `quick-picks-mobile` | 390×844 | One decision, poster beside the title, all three actions above the fold |
| `quick-picks-tablet` | 768×1024 | The same decision with the progress panel below the card |
| `quick-picks-desktop` | 1440×1000 | Decision and progress side by side |
| `quick-picks-learned-desktop` | 1440×1000 | Learned copy, shown only because the returned policy reported `learned: true` |
| `quick-picks-mutation-failure-mobile` | 390×844 | A failed decision: the card stays, the controls come back |
| `quick-picks-mutation-failure-desktop` | 1440×1000 | The same failure with the polite status line |
| `quick-picks-queue-error-desktop` | 1440×1000 | The queue read itself failing, with a retry and the request ID |
| `quick-picks-reduced-motion-desktop` | 1440×1000 | `prefers-reduced-motion` honoured: no card fling |

The cold-start captures deliberately show `Popular while we learn` with two of
five signals recorded. Progress is never rendered as a promise that serving has
switched; the learned capture exists because a response said so.

Automated Playwright coverage in `web/e2e/quick-picks.spec.ts` proves that the
button, keyboard, and gesture paths produce identical canonical outcomes, that
undo restores a dismissed title, that a failed decision restores focus, and
that every decision control clears 44×44 at 390 width. The claim that a
decision actually changed serving is proved by the service-backed journey in
`web/tests/e2e/browser-auth.spec.ts`, which runs against the seeded Compose
stack in CI.
