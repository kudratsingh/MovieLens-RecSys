"use client";

/**
 * The recorded wiring for Quick Picks.
 *
 * Only the isolated preview route and the screenshot harness mount this. The
 * queue itself arrives already tagged by the 5A fixture gate on the server, so
 * this component decides nothing about whether fixtures are allowed — it only
 * supplies a simulated durable boundary for decisions made against them.
 */

import { useMemo } from "react";

import { QuickPicksDeck } from "@/components/quick-picks/quick-picks-deck";
import { createFixtureQuickPickTransport } from "@/lib/quick-picks/fixture-transport";
import { fixtureMovieTitle } from "@/lib/quick-picks/fixtures";
import type { QuickPickQueuePayload } from "@/lib/quick-picks/transport";

export function FixtureQuickPicks({
  browseHref = "/browse",
  failCommits = false,
  initial,
}: {
  browseHref?: string;
  failCommits?: boolean;
  initial: QuickPickQueuePayload;
}) {
  const transport = useMemo(
    () =>
      createFixtureQuickPickTransport({
        failCommits,
        initial,
        resolveSeedTitle: fixtureMovieTitle,
      }),
    [failCommits, initial],
  );

  return (
    <QuickPicksDeck browseHref={browseHref} initial={initial} transport={transport} />
  );
}
