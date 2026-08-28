"use client";

/**
 * The recorded detail view, wired to an in-memory write path.
 *
 * A client boundary exists here for one reason: the page above is a Server
 * Component, and a `MovieStateClient` is an object of functions, which cannot
 * cross that boundary as a prop. Building it here — from data that can cross —
 * is what lets the preview show the states a commit produces without the live
 * route learning anything about fixtures.
 */

import { useState } from "react";

import { MovieDetailView } from "@/components/movie/movie-detail-view";
import type { MovieDetailItem } from "@/lib/api";
import { createPreviewMovieStateClient } from "@/lib/fixtures/movie-state-preview";

export function PreviewMovieDetail({
  item,
  requestId,
  userId,
  backHref,
}: {
  item: MovieDetailItem;
  requestId: string;
  userId: number;
  backHref: string;
}) {
  // Created once per movie rather than per render: the client holds the
  // committed record, and a fresh one on every render would forget the write
  // that was just made.
  const [client] = useState(() => createPreviewMovieStateClient(item.state));

  return (
    <MovieDetailView
      backHref={backHref}
      item={item}
      requestId={requestId}
      stateClient={client}
      userId={userId}
    />
  );
}
