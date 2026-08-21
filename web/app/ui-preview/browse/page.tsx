import type { Metadata } from "next";

import { BrowseExplorer } from "@/components/browse/browse-explorer";
import { ErrorState } from "@/components/ui/resource-states";
import { fixtureFailures, movies, recordedResource } from "@/lib/fixtures/movie-fixtures";

export const metadata: Metadata = { title: "Browse" };

export default async function BrowsePage({
  searchParams,
}: {
  searchParams: Promise<{ fail?: string | string[] }>;
}) {
  const failed = fixtureFailures((await searchParams).fail);
  const catalog = recordedResource("catalog", movies, failed);

  return (
    <div className="app-page">
      <header className="max-w-4xl">
        <p className="eyebrow">Browse the shelves</p>
        <h1 className="display-title mt-3 mb-0">Every good detour starts with a title.</h1>
        <p className="muted mt-5 max-w-2xl leading-7">
          Search the recorded layout fixture. The live Browse route retains the
          real paginated catalog and durable state overlay.
        </p>
      </header>
      {catalog.status === "ready" ? (
        <BrowseExplorer movies={catalog.data} />
      ) : (
        <div className="mt-10">
          <ErrorState label="Catalog" message={catalog.message} />
        </div>
      )}
    </div>
  );
}
