import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LiveQuickPicks } from "@/components/quick-picks/live-quick-picks";
import { AppShell } from "@/components/shell/app-shell";
import { auth } from "@/auth";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { resolveDemoPersonaId } from "@/lib/demo-persona";
import { personaDisplayName } from "@/lib/discover/persona";
import { productNavigationItems, routeReturnHref, signInHref } from "@/lib/navigation";
import { loadQuickPickQueue } from "@/lib/quick-picks/server";
import { refreshQuickPickQueue } from "./actions";
import "@/components/shell/shell.css";

export const metadata: Metadata = {
  title: "Quick picks",
  description:
    "Decide on one movie at a time and watch the cold-start signal count move.",
};

/**
 * Quick Picks is a focused surface, not a surface outside the product.
 *
 * It shipped without the shell, so it had no `<main>`, no skip link, neither
 * navigation, no sign-out, and a `Demo persona 900000101` placeholder where
 * every other route names the persona — leaving `Exit to Browse` as the only
 * way out and Library two navigations away. That was finish-gate item B3,
 * cleared for Browse and movie detail in the cutover and never applied here.
 *
 * Wrapping it changes nothing about the deck: the design contract lets Quick
 * Picks stay a Discover entry point rather than a fourth navigation slot, and
 * the deck's own full-height composition already reserves the shell header's
 * 5rem. What it gains is the four landmarks and the one exit every other route
 * has.
 */
export default async function QuickPicksPage({
  searchParams,
}: {
  searchParams: Promise<{ user?: string | string[] }>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  if (!session?.user || session.error) {
    redirect(signInHref(routeReturnHref("/quick-picks", params)));
  }

  const userId = resolveDemoPersonaId(params.user);
  const actorName = session.user.name ?? session.user.email ?? "Signed-in actor";

  // Started together: the persona name is shell chrome and must not stand in
  // front of the decision the route exists for. It replaces the client-side
  // `/api/personas` upgrade the deck used to paper the placeholder over with,
  // and the shell is now the only surface that prints it.
  const [initial, personaName] = await Promise.all([
    loadQuickPickQueue(userId),
    personaDisplayName(requireApiAccessToken(session), userId),
  ]);

  return (
    <AppShell
      actorName={actorName}
      fixtureMode={false}
      homeHref={`/discover?userId=${userId}`}
      homeLabel="MovieLens — For you"
      legacyHref="/legacy"
      navigationItems={productNavigationItems(userId)}
      personaLabel="Exploring as"
      personaName={personaName}
      wordmarkSubtitle="Recommendation lab"
    >
      <LiveQuickPicks
        browseHref={`/browse?user=${userId}`}
        initial={initial}
        // Bound on the server so a caller cannot retarget the read by argument.
        loadQueue={refreshQuickPickQueue.bind(null, userId)}
        userId={userId}
      />
    </AppShell>
  );
}
