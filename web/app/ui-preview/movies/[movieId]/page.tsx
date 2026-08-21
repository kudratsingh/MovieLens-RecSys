import type { Metadata } from "next";

import { MovieDetailView } from "@/components/movie/movie-detail-view";
import { ResourceRegion } from "@/components/ui/resource-region";
import type { MovieDetailResponse } from "@/lib/api";
import {
  recordedCatalogItem,
  RECORDED_CATALOG_USER_ID,
} from "@/lib/fixtures/catalog-fixtures";
import { fixtureFailures } from "@/lib/fixtures/movie-fixtures";
import {
  fixtureResourceState,
  injectedResourceFailure,
  FIXTURE_REQUEST_ID,
} from "@/lib/resources/fixture-gate";
import { hasResourceData } from "@/lib/resources/state";

export const metadata: Metadata = { title: "Movie detail" };

/**
 * The isolated preview of movie detail, served through 5A's fixture gate.
 *
 * The gate is the point: asking for recorded data outside the explicit preview
 * mode throws rather than returning it, so this page cannot become a quiet
 * fallback for a failed production read.
 */
export default async function MovieDetailPreviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ movieId: string }>;
  searchParams: Promise<{ fail?: string | string[] }>;
}) {
  const [{ movieId }, query] = await Promise.all([params, searchParams]);
  const failures = fixtureFailures(query.fail);
  const item = /^\d+$/.test(movieId)
    ? recordedCatalogItem(Number(movieId))
    : undefined;

  const state =
    !item || failures.includes("movie-detail")
      ? injectedResourceFailure("movie-detail", {
          status: "not-found",
          reason: "not-found",
        })
      : fixtureResourceState<MovieDetailResponse>("movie-detail", {
          tenant_id: "demo",
          user_id: RECORDED_CATALOG_USER_ID,
          item,
        });

  return (
    <div className="app-page">
      <ResourceRegion state={state}>
        {(detail) => (
          <MovieDetailView
            backHref="/ui-preview/browse"
            item={detail.item}
            requestId={hasResourceData(state) ? state.requestId : FIXTURE_REQUEST_ID}
            userId={RECORDED_CATALOG_USER_ID}
          />
        )}
      </ResourceRegion>
    </div>
  );
}
