import type { Metadata } from "next";

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

import { PreviewMovieDetail } from "./preview-detail";

export const metadata: Metadata = { title: "Movie detail" };

/**
 * The isolated preview of movie detail, served through 5A's fixture gate.
 *
 * The gate is the point: asking for recorded data outside the explicit preview
 * mode throws rather than returning it, so this page cannot become a quiet
 * fallback for a failed production read.
 *
 * The recorded catalog carries one movie per branch of this route, so every
 * state in the evidence matrix is an address rather than a scripted
 * interaction:
 *
 * | Address | What it shows |
 * |---|---|
 * | `/ui-preview/movies/101` | Full enrichment: backdrop hero, tagline, runtime, TMDB score, six cast, trailer. Unrated, so the stars are open. |
 * | `/ui-preview/movies/103` | Enriched with no trailer and no backdrop: the hero degrades to poster-left while credits and score stay. Rated 4.5, so the chip is showing. |
 * | `/ui-preview/movies/104` | Backdrop and trailer, rated 5: the collapsed chip over a backdrop hero. |
 * | `/ui-preview/movies/111` | `details: null` — the page this route rendered before any of this. |
 * | `/ui-preview/movies/999999` | The shared not-found state. |
 * | `?fail=movie-detail` | The injected failure state. |
 *
 * The rating's preview and reopened states are interactions rather than
 * addresses, and are covered in `e2e/movie-detail.spec.ts`.
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
        {/*
          Two of the states this preview exists to show are the *result* of a
          write, so the recorded surface needs a write path that answers. The
          client that provides it is built inside `PreviewMovieDetail`, because
          an object of functions cannot cross a Server Component boundary.
        */}
        {(detail) => (
          <PreviewMovieDetail
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
