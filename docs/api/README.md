# API contracts

`openapi.json` is the committed, generated contract for the authenticated
FastAPI surface. **Do not edit it by hand** — it is produced from the running
app's route and Pydantic definitions, and a hand edit is overwritten by the next
regeneration and caught by CI in between.

For a readable tour of what the surface actually offers — every path, the auth
rules, the rate-limit headers, and a worked recommendation response — read
[`overview.md`](overview.md). This page is about the artifact itself.

## Regenerating and checking

Regenerate after changing a route, a Pydantic model, or a response description:

```bash
make api-contract      # writes docs/api/openapi.json
make web-api-types     # writes web/lib/api.generated.ts from it
```

Verify without writing — this is what CI runs, and what to run before pushing:

```bash
make api-contract-check
make web-api-types-check
```

Both are drift checks: they regenerate into a temporary location and fail on any
difference. A red `api-contract-check` means the committed schema no longer
describes the code; a red `web-api-types-check` means the TypeScript was not
regenerated after the schema moved.

## What the artifact carries

Stable operation IDs, the Keycloak bearer security scheme, shared error
responses, and request/response constraints. The generated frontend types are
the only thing `web/` may consume — no importing Python models, and no separate
hand-written interpretation of the same shapes that can quietly disagree.

Two conventions apply to the whole surface rather than to any one operation, so
they are documented rather than modelled in the schema:

- **`X-Request-ID` on every response.** A caller-supplied value is adopted when
  it is 1–128 printable ASCII characters with no whitespace, and otherwise
  replaced with a minted UUID — a malformed header never fails the request.
  Recommendation audits store the echoed value as `correlation_id`; `request_id`
  remains the audit row's own UUID identity, so a replayed correlation header
  cannot collide with an existing row.
- **`X-RateLimit-*` on every authenticated response**, with `429` +
  `Retry-After` when the bucket is drained (ADR 0014). The headers describe a
  per-worker bucket rather than a cluster-wide quota; the schema's `info`
  description says so, and [`overview.md`](overview.md#cross-cutting-response-conventions)
  explains why.

## `serving_policy` reason vocabulary

`reason` is free text after a stable prefix. Group on the prefix; read the rest
only when looking at a single row. The response schema is unchanged — these are
the values the existing fields take.

The table below is the vocabulary; the diagram is the order the code evaluates
it in, which is what decides which row a given request lands on.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../diagrams/serving-policy-decision.dark.svg">
  <img alt="The decision tree behind serving_policy in the order the code evaluates it: whether the tenant registers a champion at all, the cold-start threshold on positive signals, the sidecar call and its failure modes including a refused champion, whether any positive seed reached retrieval, hydration and the final exclusion sweep — with every resulting policy name, learned flag and reason prefix quoted verbatim." src="../diagrams/serving-policy-decision.svg" width="100%">
</picture>

| Prefix | `name` | `learned` | Meaning |
|---|---|---|---|
| `learned-two-stage` | `item-item-cosine+lightgbm` | `true` | Both stages ran. The reason reports the number of positive seeds retrieval **used**, which can be lower than `positive_signal_count` when a watched title is absent from the deployed candidate index. |
| `unseeded-retrieval` | `popularity-fill+lightgbm` | `false` | The user was above the cold-start threshold and the ranker ran, but no positive seed reached a candidate, so the list came from the index's popularity fill. A seedless retrieval is never reported as learned. |
| `cold-start` | `popularity` | `false` | Below `threshold` positive watched signals. |
| `no-champion` | `popularity` | `false` | The caller's tenant names no champion model in `public.tenants`, so no request in it can take the learned path however warm the user is. Evaluated *before* the cold-start threshold: telling a viewer to collect five signals would promise something the tenant cannot serve at any history size. |
| `champion-mismatch` | `popularity` | `false` | The sidecar refused because the bundle it loaded is not the champion the tenant is registered on. A healthy sidecar giving a correct answer — usually a rolling deploy, permanently a promotion that moved the row without shipping the bundle. Deliberately not `model-server-unavailable`: a half-finished promotion and an outage are different things to go and fix. |
| `model-server-unavailable` | `popularity` | `false` | The sidecar failed or breached its contract. |
| `empty-learned-result` / `excluded-id-blocked` | `popularity` | `false` | Nothing survived hydration or exclusion enforcement. |

`excluded-id-blocked` also appears appended to another reason, after a `;`, when
some — but not all — ids were dropped on the way out.

The `unseeded-retrieval` row is worth dwelling on, because it is the one the
schema exists to make impossible to fake. Before PR #64, a retrieval that no
positive seed reached still reported `learned: true`; the list was the index's
popularity fill with a ranker applied to it, which is a different claim. The
distinction is now in the response rather than in a reader's head.
