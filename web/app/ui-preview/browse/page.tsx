import type { Metadata } from "next";
import { Suspense } from "react";

import { BrowseExplorer } from "@/components/browse/browse-explorer";
import { ResourceLoading } from "@/components/ui/resource-region";
import { fixtureFailures } from "@/lib/fixtures/movie-fixtures";
import { RECORDED_CATALOG_USER_ID } from "@/lib/fixtures/catalog-fixtures";

export const metadata: Metadata = { title: "Browse" };

/**
 * The isolated preview of Browse.
 *
 * It mounts the same component the authenticated route mounts and points it at
 * the recorded catalog endpoint instead of the persona's. That is what makes
 * the evidence captured here worth anything: search, filters, cursor
 * continuation, restoration, and every failure state are the real ones, not a
 * static picture of them.
 */
export default async function BrowsePreviewPage({
  searchParams,
}: {
  searchParams: Promise<{ fail?: string | string[] }>;
}) {
  const failures = fixtureFailures((await searchParams).fail);
  const injected = failures.find((name) => name.startsWith("catalog"));

  return (
    <div className="app-page">
      <header className="max-w-3xl">
        <p className="eyebrow">Browse the shelves</p>
        <h1 className="display-title mt-3 mb-0">
          Every good detour starts with a title.
        </h1>
        <p className="muted mt-5 leading-7">
          A recorded catalog answering the real query contract: composable
          filters, three sort orders, and an opaque cursor bound to the query it
          was issued for.
        </p>
      </header>

      <Suspense fallback={<ResourceLoading label="Catalog" lines={4} />}>
        <BrowseExplorer
          browsePath="/ui-preview/browse"
          catalogEndpoint={
            injected
              ? `/api/ui-preview/catalog?fail=${encodeURIComponent(injected)}`
              : "/api/ui-preview/catalog"
          }
          movieBasePath="/ui-preview/movies"
          userId={RECORDED_CATALOG_USER_ID}
        />
      </Suspense>
    </div>
  );
}
