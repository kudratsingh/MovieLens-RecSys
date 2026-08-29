# Diagrams

Ten diagrams of the system as it exists on `main`. Each one is a Mermaid source
in [`src/`](src/) and a committed pair of rendered SVGs — a light file and a
`.dark.svg` file — that documents and the README embed through `<picture>`.

## The set

| Diagram | What it shows |
|---|---|
| [`system-overview`](system-overview.svg) | The whole system on one page: browser → Next.js BFF → FastAPI middleware chain → coordinator → the private model-server sidecar, with Postgres behind pgBouncer, Keycloak, Redis, and the offline lane that produces the serving bundle. |
| [`online-request-path`](online-request-path.svg) | One authenticated `GET /users/{id}/recommendations` as a sequence: request-id adoption, token verification and tenant derivation, the rate-limit bucket, the state read, the learned-or-fallback branch, the audit insert, and the commit that precedes the response. Carries the measured latency figures and their dates. |
| [`tenancy-and-auth`](tenancy-and-auth.svg) | The isolation mechanism: issuer → tenant, the impersonation gate, the four Postgres identities, pgBouncer's transaction pool, the seven forced-RLS tables against the deliberately shared ones, and the two cross-tenant canaries. |
| [`offline-training-to-serving`](offline-training-to-serving.svg) | MovieLens 25M through ingest, the temporal split, point-in-time features, both model stages, the SHA-256-pinned serving manifest, and how that bundle reaches a running sidecar. |
| [`production-topology`](production-topology.svg) | The single Hetzner CX22: the two arrows that cross the box, the ten long-lived services, the jobs profile, the systemd units, and what is deliberately absent. |
| [`ci-cd-pipeline`](ci-cd-pipeline.svg) | The twelve CI jobs and what each gates, the GHCR publish, the deploy gate that re-asserts them by name, the release sequence on the box, and the automatic rollback. |
| [`postgres-identities`](postgres-identities.svg) | Which service connects as which Postgres role, and through which pgBouncer alias. There is no single `DATABASE_URL` in this system, and this is why. |
| [`data-model`](data-model.svg) | Every table, column and key, with the forced-RLS ones marked. Deliberately tall rather than wide — fifteen entities laid out left-to-right would be unreadable at any width a page will give it. |
| [`frontend-map`](frontend-map.svg) | Routes, the shared shell, the seventeen BFF handlers, the one server-owned resource client and its state model, the one write path, and the test layers. |
| [`serving-policy-decision`](serving-policy-decision.svg) | The decision tree behind `serving_policy`, in the order the code evaluates it, with every policy and reason string quoted verbatim from `src/serving/`. |

## Facts win over prose

Each `.mmd` opens with a comment naming the files its facts came from. Those
files are the authority. Where a diagram and a written description disagree, the
code and the migrations settle it and the diagram is corrected — a diagram that
flatters the system is worse than no diagram, because it is believed.

Two consequences of that rule are visible in the set. Nothing that does not
exist on `main` is drawn: no Prefect, no Evidently, no model registry, no A/B
router, no `/metrics` endpoint. And where something exists but is not used —
pgBouncer's `movielens_admin` alias, which no production service routes
through — the diagram says so rather than omitting it.

## Re-rendering

```sh
make diagrams          # or: cd web && npm run diagrams
```

The renderer is [`web/scripts/render-diagrams.mjs`](../../web/scripts/render-diagrams.mjs).
It needs `web/node_modules` (`make web-install`) and the Playwright Chromium the
browser suites already install; nothing else. It loads the pinned `mermaid`
package into a Chromium page served over a throwaway loopback server, renders
every source twice — once per theme — and post-processes each result.

Three things about the output are deliberate:

- **It is deterministic.** Mermaid seeds element ids and its rough.js outlines
  randomly by default, so the config pins `deterministicIds`,
  `deterministicIDSeed` and `handDrawnSeed`, and a normalisation pass rewrites
  anything run-scoped that survives. Re-rendering an unchanged source leaves
  `git status` clean; that is the check to run after editing the renderer.
- **Every label is a real SVG `<text>`.** `htmlLabels` is off at both the
  top level and per diagram type, because GitHub's image proxy renders
  `foreignObject` unreliably and a label that disappears in the README is worse
  than one that wraps badly.
- **The background is painted.** Each file carries an explicit background rect
  in its own mode's colour, so a light diagram opened in a dark context is not
  a transparent hole. The palette is read across from `web/app/globals.css`, so
  a diagram and a screenshot of the product look like one system.

## Embedding

SVGs are committed rather than left as ```` ```mermaid ```` fences because
GitHub renders `<img>` SVGs everywhere a fence is not rendered at all: inside a
`<picture>` for dark mode, in the repository social card, and in any viewer
that is not github.com. The cost is a rendered artifact in review diffs; the
benefit is that the diagram is visible wherever the document is read.

Use this snippet, with paths relative to the embedding file — from
`docs/architecture.md` the prefix is `diagrams/`, from the repository README it
is `docs/diagrams/`:

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/diagrams/system-overview.dark.svg">
  <img alt="…one-sentence description…" src="docs/diagrams/system-overview.svg" width="100%">
</picture>
```

The `alt` text is not optional. It is the only version of the diagram a screen
reader, a text-mode client, or a failed image load will get.
