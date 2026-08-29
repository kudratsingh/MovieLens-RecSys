# Bundle 7D cutover evidence

What the cutover changed, recaptured. This is deliberately not a second copy of
the [7A matrix](../bundle-7a/README.md) — that matrix is still the finish
gate's screenshot set, and it is still valid for every state it covers. Only
the surfaces this PR moved are here.

Every capture is **service-backed**: the seeded Compose stack with
`DEV_AUTH_BYPASS=false`, real Keycloak, real FastAPI, real RLS, the local
catalog snapshot, the feature and model servers, and the web BFF. Nothing here
needed failure injection, because none of these is a state a healthy stack
cannot be asked to hold still in.

**Which tree these were captured from, because it is not this branch alone.**
[PR #64](https://github.com/kudratsingh/MovieLens-RecSys/pull/64) merged to
`main` while this branch was in review, and it changed which titles every
persona is served. These images come from a stack built from `origin/main` at
`fb459dc` — #64 included — plus this branch's `web/`, which is what this branch
becomes once it is rebased. Capturing on the branch's own base would have
committed pictures of recommendations `main` no longer serves. Running the
command below after the rebase reproduces these; running it before the rebase
does not. The reasoning is in the review's
[§10.10](../../records/finish-gate-passes.md#1010-verified-against-main-after-pr-64).

## Capture command

```bash
make demo-up
make demo-seed
cd web
MOVIELENS_DEMO_URL=http://localhost:3001 npm run evidence:bundle7d
```

The script prints the warm persona's `serving_policy` before it captures, so
the run says which policy the legacy panel was reporting rather than leaving a
reader to trust the picture.

## Matrix

| Surface | Files | What it shows |
|---|---|---|
| Sign-in door | `sign-in-door-{mobile,desktop}` | The only thing an unauthenticated visitor reaches, rebuilt on the product's design tokens after the accessibility gate found two contrast failures and a 320px overflow on it |
| Front door, signed in | `landing-after-sign-in-{mobile,tablet,desktop}` | `/` now answers with the movie-discovery product — `The Shawshank Redemption`, ranked by the learned model, with one filled action. Read against `../bundle-7a/landing-after-sign-in-*`, which is the dashboard it replaces — that pair is the whole of B1 |
| Legacy dashboard | `legacy-dashboard-{mobile,desktop}` | The pre-redesign dashboard in its new home: labelled as legacy, linked back to the product, and reporting the serving policy the response carried — `item-item-cosine+lightgbm`, learned, `over 8 positive seeds` — instead of `Candidate policy: Popularity baseline`. Its `Explore` button is also readable again; see the review's N7 |
| Browse on the product shell | `browse-shell-mobile` | The bottom navigation the design contract requires for the three primary routes, on the route that had no mobile navigation at all |
| Movie detail on the product shell | `movie-detail-shell-mobile` | The same, plus `Exploring as` a resolved persona name where the route-owned header printed `Exploring as persona 900000101` |
| Library on the product shell | `library-shell-mobile` | Library ran a third header of its own; it is on the shared shell now, so the persona wording matches Discover's |

## What guards these surfaces afterwards

- `web/components/shell/app-shell.test.tsx` — the shared shell offers exactly
  the three primary routes in both navigations, names the persona instead of
  printing its ID, keeps the signed-in actor separate from it, and carries the
  legacy dashboard as a footer utility link rather than a fourth slot.
- `web/components/legacy/serving-contract-panel.test.tsx` — the panel reports
  the policy it was given and names no policy it was not told about.
- `web/e2e/finish-gate.spec.ts` — the signed-out front door joined the named
  accessibility matrix, so it is now swept at 390/768/1440 and at 320px on two
  font metrics like every other state.
- `web/tests/e2e/finish-gate-journey.spec.ts` — steps 1, 4, 5, and 10 carry the
  cutover: the front door lands on the product, every primary navigation points
  at `/discover`, Browse and movie detail render the shared shell with its
  mobile navigation and a resolved persona name, the legacy panel is checked
  against the response it claims to report, and `/legacy` is proven to be
  authenticated like any other route.
