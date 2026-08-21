# API contracts

`openapi.json` is the committed, generated contract for the authenticated
FastAPI surface. Do not edit it by hand.

Regenerate after changing a route or Pydantic model:

```bash
make api-contract
make web-api-types
```

CI and local verification use:

```bash
make api-contract-check
make web-api-types-check
```

The artifact includes stable operation IDs, the Keycloak bearer-security
scheme, shared error responses, and request/response constraints. Generated
frontend types must consume this file rather than importing Python models or
maintaining a separate handwritten interpretation.

Bundle 2 adds the selected-persona Library and feedback resources:

- `GET /users/{user_id}/library` with bounded, filter-bound keyset cursors;
- `GET /users/{user_id}/taste-profile` labeled `live-ratings-v1`;
- independent `watched`, `rating`, `watchlist`, and `dismissal` PUT/DELETE
  resources; and
- canonical mutation responses containing a revision and idempotency request
  ID. Rating deletion preserves watched state; deleting watched state is the
  separate history-removal resource.

Bundle 3 adds `listDemoCatalog` and `getMovieDetail`. Catalog cursors are
opaque, versioned, and bound to the active filter/sort query. Browse and detail
metadata come from the persisted local read model, overlay the complete durable
movie state, and never trigger live per-card TMDB calls.
