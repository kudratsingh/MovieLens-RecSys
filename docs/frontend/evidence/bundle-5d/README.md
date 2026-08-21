# Bundle 5D Library evidence

These captures render the live Library components — the same tabs, rows,
controls, states, and ratings summary the `/library` route uses — against the
recorded Library client in `web/lib/fixtures/library-fixtures.ts`. Recording the
client rather than the screenshots is what lets the matrix include the states a
reviewer cannot reach on demand: an empty collection, a dead collection read,
and the confirmation that guards removing watched history.

The recorded data never reaches the live route. `/library` reads through the
Bundle 5A boundary, and `web/lib/library/client.ts` imports no fixture — a unit
test asserts that structurally.

Capture command (Next.js running in fixture mode):

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3107
# In a second terminal:
MOVIELENS_UI_PORT=3107 npm run evidence:bundle5d
```

## Matrix

| Capture | Width | What it shows |
|---|---|---|
| `library-rated-mobile/tablet/desktop` | 390 / 768 / 1440 | Rated collection, counts, per-row star editing, persona labelling |
| `library-watchlist-mobile/desktop` | 390 / 1440 | Watchlist with its "organizational only" copy and a dismissed row |
| `library-watchlist-empty-mobile/desktop` | 390 / 1440 | Empty collection with a path to Browse |
| `library-history-long-mobile/desktop` | 390 / 1440 | History after the second cursor page is appended |
| `library-error-desktop` | 1440 | Failed collection read: tabs, filters, and the ratings summary stay usable, and no counts are invented |
| `library-remove-confirm-desktop` | 1440 | `Remove rating` and `Remove from history` side by side, with the consequence stated before the destructive confirmation |

Automated coverage for the same states — including the 44px mobile touch
targets, horizontal-overflow check, cursor-append de-duplication, and the
confirm/cancel focus path — is in `web/e2e/library-slice.spec.ts`, which runs at
all three widths. Accessibility assertions (zero critical or serious axe
violations) live with the component tests in
`web/components/library/*.test.tsx`.

Service-backed proof that a rating survives the round trip — created on movie
detail, found and edited in Rated and History, star deleted without losing the
watched interaction, then removed from history behind its confirmation — is in
`web/tests/e2e/browser-auth.spec.ts` and runs in the `browser-auth-e2e` CI job
against the seeded Compose stack with `DEV_AUTH_BYPASS=false`.
