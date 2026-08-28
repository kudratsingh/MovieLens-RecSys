"use client";

/**
 * The live wiring for Quick Picks.
 *
 * Kept separate from the deck so the production path imports no fixture, the
 * way `lib/resources/server.ts` does not. `quick-picks-fixture-lockout.test.ts`
 * asserts that stays true.
 */

import { useMemo } from "react";

import { QuickPicksDeck } from "@/components/quick-picks/quick-picks-deck";
import {
  createLiveQuickPickTransport,
  type QuickPickQueuePayload,
} from "@/lib/quick-picks/transport";

export function LiveQuickPicks({
  browseHref,
  initial,
  loadQueue,
  userId,
}: {
  browseHref: string;
  initial: QuickPickQueuePayload;
  /** Bound server action; the access token never leaves the server. */
  loadQueue: () => Promise<QuickPickQueuePayload>;
  userId: number;
}) {
  const transport = useMemo(
    () => createLiveQuickPickTransport({ loadQueue, userId }),
    [loadQueue, userId],
  );

  // The persona is not threaded through here. The route resolves it on the
  // server for the shell header, which is the one place the product names it.
  return (
    <QuickPicksDeck browseHref={browseHref} initial={initial} transport={transport} />
  );
}
