# Bundle 4 route-shell evidence

These captures use the typed, recorded frontend contract fixtures in
`web/lib/fixtures/movie-fixtures.ts`. They verify the visual system and route
shells without substituting fixtures for the live Bundle 2 or Bundle 3
persistence and catalog routes.

Capture command (with the Bundle 4 Next.js app running on port 3104):

```bash
cd web
MOVIELENS_UI_FIXTURE_MODE=1 npx next dev -p 3104
# In a second terminal:
npm run evidence:bundle4
```

The switch is a fail-closed development harness: production always requires a
real Auth.js session for `/ui-preview/*`.

The committed matrix includes Discover at 390, 768, and 1440 widths, Browse
and Library at mobile and desktop widths, plus an independently failed model
evidence resource that leaves the recommendation decision usable.

Automated Playwright coverage renders Discover, Browse, Library, movie detail,
and Quick Picks at 390×844, 768×1024, and 1440×1000 and asserts that each route has its
primary heading, named navigation, and no horizontal page overflow.
