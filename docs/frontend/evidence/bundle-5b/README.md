# Bundle 5B Discover state evidence

These captures show the live `/discover` route — the same route, components,
translation, and truthfulness code a signed-in session renders — driven through
its recorded scenarios so every state, including the ones that need a broken
upstream, is reachable deterministically.

The recorded payloads live in `web/lib/fixtures/discover-fixtures.ts` in the
real FastAPI response shape and reach the page only through
`web/lib/resources/fixture-gate.ts`. That gate throws outside
`MOVIELENS_UI_FIXTURE_MODE` and always throws in production, and `/discover`
reads live unless a `?demo=<scenario>` selector explicitly asks for a recorded
one. A capture that says `Recorded contract fixtures` is telling the truth
about itself.

Capture command (with the isolated harness running on port 3104):

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3104
# In a second terminal:
npm run evidence:bundle5b
```

## Matrix

Every state is captured at 390x844, 768x1024, and 1440x1000.

| State | Scenario | What it shows |
|---|---|---|
| `learned` | `?demo=learned` | Primary movie first, `Ranked by the learned model`, ranked rail, Browse path. |
| `fallback` | `?demo=fallback` | `Popular while we learn`. No learned copy anywhere on the page. |
| `empty` | `?demo=empty` | A 200 with no unseen titles, with a route forward rather than an error. |
| `loading` | `?demo=loading` | Region skeletons that announce themselves and claim no failure. |
| `auth-expired` | `?demo=auth-expired` | Expired session with a reauthentication path. |
| `partial-recommendations-error` | `?demo=recommendations-error` | Recommendations failed; watch history is still readable. |
| `partial-history-error` | `?demo=history-error` | Watch history failed; the movie decision above it is untouched. |
| `partial-evidence-error` | `?demo=evidence-error` | Audit and feature reads failed; the reason and policy still read. |
| `poster-failure` | `?demo=poster-failure` | Artwork failed to load; movie identity and layout hold. |
| `why-this` | `?demo=learned` | First disclosure step: reason, policy, versions, correlation ID. |
| `technical-evidence` | `?demo=learned` | Second and final step: prediction audit and online feature values. |

## What the captures are not

They are not proof that the live API path works — the service-backed browser
journey (`web/tests/e2e/discover-journey.spec.ts`, run by the CI
`browser-auth-e2e` job against the bypass-disabled demo stack) covers sign-in,
a committed mutation, and the refreshed recommendation request. These captures
cover visual state, responsive behaviour, and truthfulness of copy.
