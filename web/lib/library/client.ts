/**
 * Browser-side Library reads, over the shared movie-state write path.
 *
 * Reads go through the shared live-resource reader so a Library region reports
 * the same state model as every other region. Writes are not defined here at
 * all any more: a Library row changes exactly the same four resources a
 * recommendation card or a movie's own page does, so it uses the same client,
 * the same idempotency and revision rules, and the same committed-state relay.
 * The Library-specific part of a write — the optimistic collection projection,
 * the persona-voiced announcement, the focus walk — belongs to the route, not
 * to a second transport.
 *
 * `LibraryClient` composes the two so the recorded `/ui-preview` surface can
 * still supply one working object. This module imports no fixture: the recorded
 * preview supplies its own client, so there is no path by which a live route can
 * fall back to recorded data.
 */

import type { LibraryResponse, TasteSummaryResponse } from "@/lib/api";
import {
  LIBRARY_PAGE_SIZE,
  type LibrarySort,
  type LibraryTab,
} from "@/lib/library/url-state";
import {
  createBffMovieStateClient,
  type MovieStateClient,
} from "@/lib/movie-state/client";
import { readBffResource } from "@/lib/resources/browser";
import { LIBRARY, TASTE_PROFILE } from "@/lib/resources/definitions";
import type { ResourceState } from "@/lib/resources/state";

export type LibraryReadOptions = {
  userId: number;
  tab: LibraryTab;
  sort: LibrarySort;
  query: string;
  cursor: string | null;
  limit?: number;
  signal?: AbortSignal;
};

export type LibraryClient = MovieStateClient & {
  readLibrary(options: LibraryReadOptions): Promise<ResourceState<LibraryResponse>>;
  readTasteProfile(userId: number): Promise<ResourceState<TasteSummaryResponse>>;
};

export function libraryReadUrl(options: LibraryReadOptions): string {
  const params = new URLSearchParams({
    tab: options.tab,
    sort: options.sort,
    limit: String(options.limit ?? LIBRARY_PAGE_SIZE),
  });
  if (options.query) params.set("q", options.query);
  if (options.cursor) params.set("cursor", options.cursor);
  return `/api/users/${options.userId}/library?${params}`;
}

export function createBffLibraryClient(
  fetchImpl?: typeof fetch,
): LibraryClient {
  return {
    ...createBffMovieStateClient(fetchImpl),

    readLibrary(options) {
      return readBffResource(LIBRARY, libraryReadUrl(options), {
        fetchImpl,
        signal: options.signal,
      });
    },

    readTasteProfile(userId) {
      return readBffResource(
        TASTE_PROFILE,
        `/api/users/${userId}/taste-profile`,
        { fetchImpl },
      );
    },
  };
}

export const bffLibraryClient = createBffLibraryClient();
