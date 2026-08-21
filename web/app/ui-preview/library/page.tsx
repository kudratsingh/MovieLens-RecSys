import type { Metadata } from "next";

import { LibraryTabs } from "@/components/library/library-tabs";
import { ErrorState } from "@/components/ui/resource-states";
import { fixtureFailures, libraryFixture, recordedResource } from "@/lib/fixtures/movie-fixtures";

export const metadata: Metadata = { title: "Library" };

export default async function LibraryPage({
  searchParams,
}: {
  searchParams: Promise<{ fail?: string | string[]; tab?: string }>;
}) {
  const params = await searchParams;
  const failed = fixtureFailures(params.fail);
  const library = recordedResource("library", libraryFixture, failed);
  const initialTab = ["rated", "watchlist", "history"].includes(params.tab ?? "")
    ? (params.tab as "rated" | "watchlist" | "history")
    : "rated";

  return (
    <div className="app-page">
      <header className="max-w-4xl">
        <p className="eyebrow">Exploring as Action Fan</p>
        <h1 className="display-title mt-3 mb-0">A record of what moved you.</h1>
        <p className="muted mt-5 max-w-2xl leading-7">
          This recorded preview keeps ratings, saved movies, and history
          distinct. Manage canonical state through the live Library route.
        </p>
      </header>
      {library.status === "ready" ? (
        <LibraryTabs collection={library.data} initialTab={initialTab} />
      ) : (
        <div className="mt-10"><ErrorState label="Library" message={library.message} /></div>
      )}
    </div>
  );
}
