import type { Metadata } from "next";

import { FixtureQuickPicks } from "@/components/quick-picks/fixture-quick-picks";
import {
  fixtureQuickPickEvidence,
  fixtureQuickPickResponse,
} from "@/lib/quick-picks/fixtures";
import { fixtureResourceState, injectedResourceFailure } from "@/lib/resources/fixture-gate";

export const metadata: Metadata = { title: "Quick picks" };

/**
 * The isolated Quick Picks harness.
 *
 * It mounts the production deck against recorded data rather than a separate
 * mock screen, so a screenshot or a browser assertion here is evidence about
 * the shipped component. The fixture gate is what keeps it isolated: asking for
 * recorded data outside fixture mode throws instead of returning it.
 *
 * - `?fail=queue` injects an upstream failure on the initial read.
 * - `?fail=commit` fails every mutation, for the rollback state.
 * - `?policy=learned` swaps the cold-start policy for a learned one.
 */
export default async function QuickPicksPreviewPage({
  searchParams,
}: {
  searchParams: Promise<{ fail?: string; policy?: string }>;
}) {
  const params = await searchParams;
  const learned = params.policy === "learned";
  const queue =
    params.fail === "queue"
      ? injectedResourceFailure("recommendations", {
          status: "upstream-error",
          reason: "timeout",
        })
      : fixtureResourceState("recommendations", fixtureQuickPickResponse({ learned }));

  return (
    <FixtureQuickPicks
      failCommits={params.fail === "commit"}
      initial={{ queue, evidence: fixtureQuickPickEvidence(learned) }}
    />
  );
}
