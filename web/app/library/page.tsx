import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { LibraryExperience } from "@/components/library/library-experience";
import { LibraryShell } from "@/components/library/library-shell";
import type { PersonaResponse } from "@/lib/api";
import { proxyRecommendationApi } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";
import {
  LIBRARY_PAGE_SIZE,
  parseLibraryUrlState,
  type LibrarySearchParams,
} from "@/lib/library/url-state";
import { isRecord } from "@/lib/resources/validate";
import { loadLibrary, loadTasteProfile } from "@/lib/resources/server";
import "@/components/library/library.css";

export const metadata: Metadata = {
  title: "Library",
  description:
    "Review and manage the ratings, watchlist, and watched history stored for the selected demo persona.",
};

/**
 * Resolves the persona's display name for the route's identity copy.
 *
 * This is a label, not an authorization decision — the API enforces who may
 * read the persona — so a directory that cannot be read degrades to the ID
 * rather than blocking the collection.
 */
async function loadPersonaLabel(
  userId: number,
  accessToken: string,
): Promise<string | null> {
  try {
    const response = await proxyRecommendationApi(accessToken, "/personas");
    if (!response.ok) return null;
    const payload: unknown = await response.json();
    if (!isRecord(payload) || !Array.isArray(payload.items)) return null;
    const persona = (payload as PersonaResponse).items.find(
      (item) => item.user_id === userId,
    );
    return persona?.display_name ?? null;
  } catch {
    return null;
  }
}

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
    loadPersonaLabel(urlState.userId, accessToken),
  ]);

  const actorName = session.user.name ?? session.user.email ?? "Signed-in actor";
  const personaLabel = persona ?? `Persona ${urlState.userId}`;

  return (
    <LibraryShell actorName={actorName} personaLabel={personaLabel}>
      <div className="app-page">
        {library.status === "not-found" ? (
          <p className="library-notice">
            No registered demo persona matches user {urlState.userId}.{" "}
            <Link href="/">Choose a persona</Link> to open its library.
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
    </LibraryShell>
  );
}
