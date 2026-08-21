import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { DiscoverExperience } from "@/components/discover/discover-experience";
import { WatchHistory } from "@/components/discover/watch-history";
import { AppShell } from "@/components/shell/app-shell";
import { FrontendErrorBoundary } from "@/components/ui/error-boundary";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { personaDisplayName } from "@/lib/discover/persona";
import {
  DISCOVER_RECOMMENDATION_LIMIT,
  discoverScenario,
  loadDiscoverResources,
} from "@/lib/discover/resources";
import { productNavigationItems } from "@/lib/navigation";
import { hasResourceData } from "@/lib/resources/state";
import { isolatedUiPreviewMode } from "@/lib/ui-preview-access";
import "@/components/discover/discover.css";
import "@/components/shell/shell.css";

export const metadata: Metadata = { title: "For you" };

/** Personalized, tenant-scoped, and session-dependent: never prerendered. */
export const dynamic = "force-dynamic";

/** The seeded walkthrough persona, matching the other authenticated routes. */
const DEFAULT_PERSONA_ID = 900000101;

type DiscoverSearch = {
  userId?: string | string[];
  demo?: string | string[];
};

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function personaId(value: string | string[] | undefined): number {
  const requested = single(value);
  if (!requested || !/^\d+$/.test(requested)) return DEFAULT_PERSONA_ID;
  const parsed = Number(requested);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : DEFAULT_PERSONA_ID;
}

export default async function DiscoverPage({
  searchParams,
}: {
  searchParams: Promise<DiscoverSearch>;
}) {
  const params = await searchParams;
  const fixtureMode = isolatedUiPreviewMode();
  const session = fixtureMode ? null : await auth();
  if (!fixtureMode && (!session?.user || session.error)) redirect("/");

  const userId = personaId(params.userId);
  const scenario = discoverScenario(params.demo);
  const accessToken = requireApiAccessToken(session);

  // Started together: the persona name is shell chrome and must not delay the
  // first movie, and history must not stand in front of it either.
  const [resources, personaName] = await Promise.all([
    loadDiscoverResources({ session, userId, scenario }),
    fixtureMode && scenario
      ? Promise.resolve("Action Fan")
      : personaDisplayName(accessToken, userId),
  ]);

  const browseHref = `/browse?user=${userId}`;
  const recorded =
    hasResourceData(resources.recommendations) &&
    resources.recommendations.source === "recorded-contract-fixture";

  return (
    <AppShell
      actorName={
        fixtureMode
          ? "Fixture reviewer"
          : (session?.user?.name ?? session?.user?.email ?? "Signed-in actor")
      }
      fixtureMode={fixtureMode}
      homeHref={`/discover?userId=${userId}`}
      homeLabel="MovieLens — For you"
      navigationItems={productNavigationItems(userId)}
      personaLabel="Exploring as"
      personaName={personaName}
      wordmarkSubtitle="Recommendation lab"
    >
      <FrontendErrorBoundary label="Discover">
        <div className="app-page">
          {recorded ? (
            <p className="evidence-caveat" role="status">
              Recorded contract fixtures. This response did not come from the
              recommendation API.
            </p>
          ) : null}

          <DiscoverExperience
            browseHref={browseHref}
            initialRecommendations={resources.recommendations}
            limit={DISCOVER_RECOMMENDATION_LIMIT}
            movieHrefBase="/movies"
            // Movie detail only accepts a Browse return target today, so no
            // `returnTo` is sent rather than one the route would discard.
            movieHrefQuery={`?user=${userId}`}
            personaName={personaName}
            recordedEvidence={resources.recordedEvidence}
            userId={userId}
          />

          <WatchHistory
            browseHref={browseHref}
            personaName={personaName}
            state={resources.history}
          />
        </div>
      </FrontendErrorBoundary>
    </AppShell>
  );
}
