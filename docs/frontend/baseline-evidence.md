# Movie-discovery frontend: baseline evidence

**Status:** Source audit and authenticated rendered screenshot matrix complete

**Baseline revision:** `c73b967e17ebbba1d31673cbd191d9c1706e6b1d`

**Date:** 2026-08-21

**Capture:** Playwright captured the seeded, bypass-disabled Keycloak demo on
2026-08-21. The matrix is committed under
[`docs/frontend/evidence/baseline/`](evidence/baseline/). The authenticated
wrapper adds actor/logout controls, while the dashboard under evaluation
remains the pre-redesign rating-first baseline.

## Purpose

The redesign must be judged against visible evidence, not adjectives. This file
records the current source-backed baseline and the exact screenshots/states
required before the first visual implementation change and before the final
finish gate.

## Source-backed baseline

| Observation | Evidence |
|---|---|
| Architecture/phase content precedes movie content | `web/app/page.tsx` hero and serving-contract section |
| Serving-contract copy says `Popularity baseline` | `web/app/page.tsx` candidate-policy row |
| Rating Studio renders before recommendations | `Dashboard` in `web/components/recommendation-demo.tsx` |
| Rating Studio shows up to 18 text-only catalog entries | `RatingStudio` in `web/components/recommendation-demo.tsx` |
| Browser-facing dashboard requests 8 recommendations and 8 history entries | `web/app/api/users/[userId]/route.ts` |
| Catalog frontend type has no poster, overview, year, or state beyond rating | `web/lib/api.ts` |
| Catalog query has no pagination/search/filter and uses `LIMIT 100` | `src/serving/recommendations.py` |
| Clean demo fixture contains 24 movies | `synthetic/personas/catalog.json` |
| Frontend has no component or browser test scripts | `web/package.json` |

## Baseline screenshot matrix

The current seeded demo was captured before the first visual redesign. Stable
persona fixtures were used and no nondeterministic animation is present.

| Viewport | Persona/state | Required evidence |
|---|---|---|
| 1440×1000 | Action Fan | First viewport and full page |
| 1440×1000 | Cold Start | First viewport, popularity fallback, empty history |
| 768×1024 | Action Fan | First viewport and rating/recommendation relationship |
| 390×844 | Action Fan | Five-second-test viewport and full page |
| 390×844 | Cold Start | Empty-history/fallback behavior |
| 390×844 | API failure | Error, retry, and retained navigation/context |
| 390×844 | Poster failure | Movie fallback without layout collapse |

Store baseline captures under:

```text
docs/frontend/evidence/baseline/
```

Use names such as:

```text
action-desktop-first-viewport.png
action-mobile-full-page.png
cold-mobile-first-viewport.png
api-error-mobile.png
poster-fallback-mobile.png
```

## Five-second baseline questions

For the mobile first viewport, record unprompted answers to:

1. What is this product?
2. Is it for you?
3. What should you do first?

The current hypothesis is that the page communicates an ML portfolio project
before it communicates a movie decision. Treat that as a hypothesis until the
capture and walkthrough evidence exist.

## Implementation evidence

Each frontend bundle adds matching captures under:

```text
docs/frontend/evidence/<bundle-name>/
```

Every claim in a PR description should link to a screenshot, automated test,
or recorded state. `Clean`, `modern`, `premium`, and `polished` are not evidence.

## Final comparison

The final finish gate must compare the baseline and candidate at the same
viewport, persona, data revision, and TMDB mode. The comparison should answer:

- Is a movie now the first-read object?
- Is the primary action visible?
- Can more movies be scanned with less rating-form noise?
- Did ML evidence remain discoverable?
- Did mobile preserve the workflow rather than merely stack desktop content?
- Are loading, empty, error, and fallback states at least as informative as the
  baseline?
