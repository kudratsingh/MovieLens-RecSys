import type { Metadata } from "next";

import { RecordedLibrary } from "@/components/library/recorded-library";
import {
  createRecordedLibraryClient,
  type RecordedLibraryOptions,
} from "@/lib/fixtures/library-fixtures";
import { fixtureFailures } from "@/lib/fixtures/movie-fixtures";
import {
  LIBRARY_PAGE_SIZE,
  isLibraryTab,
  parseLibraryUrlState,
  type LibrarySearchParams,
  type LibraryTab,
} from "@/lib/library/url-state";
import "@/components/library/library.css";

export const metadata: Metadata = { title: "Library" };

function tabList(value: string | string[] | undefined): LibraryTab[] {
  return fixtureFailures(value).filter(isLibraryTab);
}

/**
 * The recorded Library surface used by the responsive and screenshot harnesses.
 *
 * `?empty=` and `?fail=` inject the states that are otherwise hard to reach on
 * demand — an empty collection, a dead Library read, a dead ratings summary —
 * so the evidence matrix can be captured deterministically instead of being
 * described in prose.
 */
export default async function RecordedLibraryPage({
  searchParams,
}: {
  searchParams: Promise<LibrarySearchParams & { empty?: string; fail?: string }>;
}) {
  const params = await searchParams;
  const urlState = parseLibraryUrlState(params);
  const failed = fixtureFailures(params.fail);
  const options: RecordedLibraryOptions = {
    emptyTabs: tabList(params.empty),
    failing: failed.filter(
      (name): name is "library" | "taste-profile" =>
        name === "library" || name === "taste-profile",
    ),
  };

  const client = createRecordedLibraryClient(options);
  const [library, taste] = await Promise.all([
    client.readLibrary({
      userId: urlState.userId,
      tab: urlState.tab,
      sort: urlState.sort,
      query: urlState.query,
      cursor: urlState.cursor,
      limit: LIBRARY_PAGE_SIZE,
    }),
    client.readTasteProfile(urlState.userId),
  ]);

  return (
    <div className="app-page">
      <RecordedLibrary
        initialLibrary={library}
        initialTaste={taste}
        initialUrlState={urlState}
        options={options}
        urlExtras={{
          empty: options.emptyTabs?.join(",") ?? "",
          fail: options.failing?.join(",") ?? "",
        }}
      />
    </div>
  );
}
