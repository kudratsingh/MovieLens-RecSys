import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LiveQuickPicks } from "@/components/quick-picks/live-quick-picks";
import { auth } from "@/auth";
import { loadQuickPickQueue } from "@/lib/quick-picks/server";
import { refreshQuickPickQueue, resolveQuickPickSeedTitle } from "./actions";

export const metadata: Metadata = {
  title: "Quick picks",
  description:
    "Decide on one movie at a time and watch the cold-start signal count move.",
};

const DEFAULT_PERSONA_ID = 900000101;

export default async function QuickPicksPage({
  searchParams,
}: {
  searchParams: Promise<{ user?: string }>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  if (!session?.user || session.error) redirect("/");

  const userId = /^\d+$/.test(params.user ?? "")
    ? Number(params.user)
    : DEFAULT_PERSONA_ID;
  const initial = await loadQuickPickQueue(userId);

  return (
    <LiveQuickPicks
      browseHref={`/browse?user=${userId}`}
      initial={initial}
      // Bound on the server so a caller cannot retarget the read by argument.
      loadQueue={refreshQuickPickQueue.bind(null, userId)}
      loadSeedTitle={resolveQuickPickSeedTitle.bind(null, userId)}
      personaLabel={`Demo persona ${userId}`}
      userId={userId}
    />
  );
}
