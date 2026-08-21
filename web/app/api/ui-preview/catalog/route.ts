/**
 * The recorded catalog endpoint behind the isolated UI preview.
 *
 * Browse's paging behaviour only exists across requests, so reviewing it —
 * and screenshotting it — needs something that answers the catalog contract
 * over HTTP. This route is that something, and it is the *only* door: the
 * client component fetches one endpoint either way, so what the preview
 * renders is the same component, in the same states, as the authenticated
 * route.
 *
 * It is fail-closed by construction. `isolatedUiPreviewMode` requires an
 * explicit development-only flag, so a production build answers `404` here no
 * matter how the route is called, and `ui-preview-catalog.test.ts` asserts it.
 * That is what keeps this a declared preview surface rather than the silent
 * fixture fallback the handoff rules out.
 */

import {
  queryRecordedCatalog,
  RecordedCursorRejected,
} from "@/lib/fixtures/catalog-fixtures";
import {
  CATALOG_PAGE_LIMIT,
  CATALOG_PAGE_LIMIT_MAX,
  parseBrowseQuery,
} from "@/lib/browse/query";
import { PRIVATE_NO_STORE, resourceRequestId } from "@/lib/resources/bff";
import { REQUEST_ID_HEADER } from "@/lib/resources/request-id";
import { isolatedUiPreviewMode } from "@/lib/ui-preview-access";

/** Deliberate failure injection, so the preview can show real error states. */
const INJECTED: Record<string, { status: number; detail: string }> = {
  catalog: {
    status: 502,
    detail: "Injected upstream failure for the recorded catalog preview.",
  },
  "catalog-auth": {
    status: 401,
    detail: "Your session has expired. Sign in again.",
  },
  "catalog-forbidden": {
    status: 403,
    detail: "This session is not allowed to read that resource.",
  },
};

export async function GET(request: Request): Promise<Response> {
  if (!isolatedUiPreviewMode()) {
    return new Response(null, { status: 404 });
  }

  const url = new URL(request.url);
  const requestId = resourceRequestId(request);
  const headers = new Headers({
    "Cache-Control": PRIVATE_NO_STORE,
    [REQUEST_ID_HEADER]: requestId,
  });

  const injected = INJECTED[url.searchParams.get("fail") ?? ""];
  if (injected) {
    return Response.json(
      { detail: injected.detail },
      { status: injected.status, headers },
    );
  }

  const query = parseBrowseQuery(url.searchParams);
  const requestedLimit = Number(url.searchParams.get("limit") ?? CATALOG_PAGE_LIMIT);
  const limit = Number.isFinite(requestedLimit)
    ? Math.min(Math.max(1, Math.trunc(requestedLimit)), CATALOG_PAGE_LIMIT_MAX)
    : CATALOG_PAGE_LIMIT;

  try {
    return Response.json(
      queryRecordedCatalog({
        q: query.q || null,
        genre: query.genre,
        yearFrom: query.yearFrom,
        yearTo: query.yearTo,
        sort: query.sort,
        limit,
        cursor: query.cursor,
      }),
      { headers },
    );
  } catch (error) {
    if (error instanceof RecordedCursorRejected) {
      // The same 400 the endpoint returns when a cursor outlives its query.
      return Response.json({ detail: error.message }, { status: 400, headers });
    }
    throw error;
  }
}
