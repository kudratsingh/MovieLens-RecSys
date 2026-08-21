"use client";

/**
 * The live wiring for Quick Picks.
 *
 * Kept separate from the deck so the production path imports no fixture, the
 * way `lib/resources/server.ts` does not. `quick-picks-fixture-lockout.test.ts`
 * asserts that stays true.
 */

import { useEffect, useMemo, useState } from "react";

import { QuickPicksDeck } from "@/components/quick-picks/quick-picks-deck";
import type { PersonaResponse } from "@/lib/api";
import {
  createLiveQuickPickTransport,
  type QuickPickQueuePayload,
} from "@/lib/quick-picks/transport";

export function LiveQuickPicks({
  browseHref,
  initial,
  loadQueue,
  personaLabel,
  userId,
}: {
  browseHref: string;
  initial: QuickPickQueuePayload;
  /** Bound server action; the access token never leaves the server. */
  loadQueue: () => Promise<QuickPickQueuePayload>;
  personaLabel: string;
  userId: number;
}) {
  const transport = useMemo(
    () => createLiveQuickPickTransport({ loadQueue, userId }),
    [loadQueue, userId],
  );

  // The persona's display name is nice-to-have context, not part of the
  // decision, so it is upgraded after the queue is on screen and the numeric
  // label stands if the lookup fails.
  const [resolvedLabel, setResolvedLabel] = useState(personaLabel);
  useEffect(() => {
    let current = true;
    void fetch("/api/personas", { cache: "no-store" })
      .then((response) => (response.ok ? (response.json() as Promise<PersonaResponse>) : null))
      .then((payload) => {
        const persona = payload?.items.find((item) => item.user_id === userId);
        if (current && persona) setResolvedLabel(persona.display_name);
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [userId]);

  return (
    <QuickPicksDeck
      browseHref={browseHref}
      initial={initial}
      personaLabel={resolvedLabel}
      transport={transport}
    />
  );
}
