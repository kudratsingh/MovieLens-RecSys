import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { LibraryExperience } from "@/components/library/library-experience";
import { AppShell } from "@/components/shell/app-shell";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { DEFAULT_DEMO_PERSONA_ID } from "@/lib/demo-persona";
import { fallbackPersonaName, resolvePersonaName } from "@/lib/discover/persona";
import {
  LIBRARY_PAGE_SIZE,
  parseLibraryUrlState,
  type LibrarySearchParams,
} from "@/lib/library/url-state";
import { productNavigationItems } from "@/lib/navigation";
import { loadLibrary, loadTasteProfile } from "@/lib/resources/server";
import "@/components/library/library.css";
import "@/components/shell/shell.css";

export const metadata: Metadata = {
  title: "Library",
  description:
    "Review and manage the ratings, watchlist, and watched history stored for the selected demo persona.",
};

export default async function LibraryPage({
  searchParams,
}: {
  searchParams: Promise<LibrarySearchParams>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  if (!session?.user || session.error) redirect("/");
  const accessToken = requireApiAccessToken(session);
  if (!accessToken) redirect("/");

  const urlState = parseLibraryUrlState(params);

  // Three independent reads. The collection, the ratings summary, and the
  // persona label each fail on their own terms; none of them can blank the
  // other two.
  const [library, taste, persona] = await Promise.all([
    loadLibrary(urlState.userId, {
      session,
      query: {
        tab: urlState.tab,
        sort: urlState.sort,
        limit: LIBRARY_PAGE_SIZE,
        cursor: urlState.cursor ?? undefined,
        q: urlState.query || undefined,
      },
    }),
    loadTasteProfile(urlState.userId, { session }),
    resolvePersonaName(accessToken, urlState.userId),
  ]);

  const actorName = session.user.name ?? session.user.email ?? "Signed-in actor";
  const personaLabel = persona ?? fallbackPersonaName(urlState.userId);

  return (
    <AppShell
      actorName={actorName}
      fixtureMode={false}
      homeHref={`/discover?userId=${urlState.userId}`}
      homeLabel="MovieLens — For you"
      legacyHref="/legacy"
      navigationItems={productNavigationItems(urlState.userId)}
      personaLabel="Exploring as"
      personaName={personaLabel}
      wordmarkSubtitle="Recommendation lab"
    >
      <div className="app-page">
        {library.status === "not-found" ? (
          <p className="library-notice">
            No registered demo persona matches user {urlState.userId}.{" "}
            <Link href={`/library?userId=${DEFAULT_DEMO_PERSONA_ID}`}>
              Open the default persona
            </Link>{" "}
            instead.
          </p>
        ) : null}
        <LibraryExperience
          actorName={actorName}
          initialLibrary={library}
          initialTaste={taste}
          initialUrlState={urlState}
          personaLabel={personaLabel}
          personaResolved={persona !== null}
        />
      </div>
    </AppShell>
  );
}
