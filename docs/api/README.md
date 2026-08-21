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
