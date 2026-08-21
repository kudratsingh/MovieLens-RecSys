import type { Metadata } from "next";
import { Suspense } from "react";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { BrowseExplorer } from "@/components/browse/browse-explorer";
import { AppShell } from "@/components/shell/app-shell";
import { ResourceLoading } from "@/components/ui/resource-region";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { resolveDemoPersonaId } from "@/lib/demo-persona";
import { personaDisplayName } from "@/lib/discover/persona";
import { productNavigationItems } from "@/lib/navigation";
import "@/components/shell/shell.css";

export const metadata: Metadata = {
  title: "Browse movies",
  description:
    "Search, filter, and page the reviewed MovieLens demo catalog with the selected persona's saved state.",
};

/**
 * Browse is authenticated and persona-scoped, so the server does the session
 * work and the catalog itself loads client-side through the BFF. That split is
 * deliberate: the grid is an accumulating cursor window whose state belongs to
 * the client, and server-rendering only the first page would make every filter
 * change and every "load more" argue with a re-rendered server payload.
 */
export default async function BrowsePage({
  searchParams,
}: {
  searchParams: Promise<{ user?: string | string[] }>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  if (!session?.user || session.error) redirect("/");

  const userId = resolveDemoPersonaId(params.user);
  const actorName = session.user.name ?? session.user.email ?? "Signed-in actor";
  // Shell chrome, not a product region: the catalog must not wait on the
  // persona directory, and a failed lookup degrades to the numeric persona
  // rather than to an error.
  const personaName = await personaDisplayName(
    requireApiAccessToken(session),
    userId,
  );

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
      <div className="app-page">
        <header className="max-w-3xl">
          <p className="eyebrow">Browse the catalog</p>
          <h1 className="display-title mt-3 mb-0">
            Every good detour starts with a title.
          </h1>
          <p className="muted mt-5 leading-7">
            Search a local metadata snapshot. Posters and synopses come from the
            reviewed catalog, never a live third-party request per card, so a
            missing record is a named gap rather than a slow page.
          </p>
        </header>

        <Suspense fallback={<ResourceLoading label="Catalog" lines={4} />}>
          <BrowseExplorer
            browsePath="/browse"
            catalogEndpoint={`/api/users/${userId}/catalog`}
            movieBasePath="/movies"
            persistedParams={{ user: String(userId) }}
            userId={userId}
          />
        </Suspense>
      </div>
    </AppShell>
  );
}
