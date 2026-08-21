"use client";

import { useMemo } from "react";

import { LibraryExperience } from "@/components/library/library-experience";
import type { LibraryResponse, TasteSummaryResponse } from "@/lib/api";
import {
  createRecordedLibraryClient,
  RECORDED_PERSONA,
  type RecordedLibraryOptions,
} from "@/lib/fixtures/library-fixtures";
import type { LibraryUrlState } from "@/lib/library/url-state";
import type { ResourceState } from "@/lib/resources/state";

/**
 * The recorded Library preview.
 *
 * It renders the same experience the live route does, over a recorded client
 * rather than the BFF, so the screenshot matrix and the responsive checks
 * exercise the real component tree. The client is built here rather than on the
 * server because a function cannot cross the server/client boundary — the
 * server page still resolves the first page so the preview has server-rendered
 * content.
 */
export function RecordedLibrary({
  initialLibrary,
  initialTaste,
  initialUrlState,
  options,
  urlExtras,
}: {
  initialLibrary: ResourceState<LibraryResponse>;
  initialTaste: ResourceState<TasteSummaryResponse>;
  initialUrlState: LibraryUrlState;
  options: RecordedLibraryOptions;
  urlExtras: Record<string, string>;
}) {
  // The knobs come from the URL, so the recorded store is rebuilt only on a
  // full navigation — a re-render keeps whatever the preview session wrote.
  const emptyKey = options.emptyTabs?.join(",") ?? "";
  const failKey = options.failing?.join(",") ?? "";
  const client = useMemo(
    () => createRecordedLibraryClient(options),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [emptyKey, failKey],
  );

  return (
    <LibraryExperience
      actorName="Fixture reviewer"
      basePath="/ui-preview/library"
      client={client}
      initialLibrary={initialLibrary}
      initialTaste={initialTaste}
      initialUrlState={initialUrlState}
      movieHref={(movieId) => `/ui-preview/movies/${movieId}`}
      personaLabel={RECORDED_PERSONA}
      personaResolved
      recordedNote="Recorded contract fixture. Canonical state is managed on the live Library route; anything changed here stays in this preview session."
      urlExtras={urlExtras}
    />
  );
}
