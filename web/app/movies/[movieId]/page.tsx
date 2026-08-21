import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { cache } from "react";

import { auth } from "@/auth";
import { MovieDetailView } from "@/components/movie/movie-detail-view";
import { AppShell } from "@/components/shell/app-shell";
import { ResourceRegion } from "@/components/ui/resource-region";
import { requireApiAccessToken } from "@/lib/bff-auth";
import { overviewText } from "@/lib/browse/catalog-card";
import { resolveDemoPersonaId } from "@/lib/demo-persona";
import { personaDisplayName } from "@/lib/discover/persona";
import {
  productNavigationItems,
  returnHrefLabel,
  safeReturnHref,
} from "@/lib/navigation";
import { loadMovieDetail } from "@/lib/resources/server";
import { hasResourceData } from "@/lib/resources/state";
import "@/components/shell/shell.css";

type DetailProps = {
  params: Promise<{ movieId: string }>;
  searchParams: Promise<{ user?: string | string[]; returnTo?: string | string[] }>;
};

/**
 * `generateMetadata` and the page body both need the record. Memoising the
 * read per request keeps that to one upstream call — and one audit row — so a
 * detail view is not silently charged twice against the latency budget.
 */
const detailForRequest = cache(async (userId: number, movieId: number) => {
  const session = await auth();
  return loadMovieDetail(userId, movieId, { session });
});

function parseMovieId(value: string): number {
  return /^\d{1,15}$/.test(value) ? Number(value) : 0;
}

export async function generateMetadata({
  params,
  searchParams,
}: DetailProps): Promise<Metadata> {
  const [{ movieId }, query, session] = await Promise.all([
    params,
    searchParams,
    auth(),
  ]);
  if (!session?.user || session.error) {
    return { title: "Movie detail", robots: { index: false } };
  }

  const state = await detailForRequest(
    resolveDemoPersonaId(query.user),
    parseMovieId(movieId),
  );
  if (!hasResourceData(state)) {
    return {
      title: "Movie unavailable",
      description: "This MovieLens catalog record is currently unavailable.",
      robots: { index: false },
    };
  }

  const item = state.data.item;
  const description = overviewText(item);
  return {
    title: item.title,
    description,
    openGraph: {
      title: item.title,
      description,
      images: item.poster_url ? [item.poster_url] : [],
    },
    twitter: {
      card: "summary",
      title: item.title,
      description,
      images: item.poster_url ? [item.poster_url] : [],
    },
  };
}

export default async function MovieDetailPage({ params, searchParams }: DetailProps) {
  const [{ movieId }, query, session] = await Promise.all([
    params,
    searchParams,
    auth(),
  ]);
  if (!session?.user || session.error) redirect("/");

  const userId = resolveDemoPersonaId(query.user);
  const actorName = session.user.name ?? session.user.email ?? "Signed-in actor";
  // Browse, Library, and Discover can all send a viewer here, and each of them
  // has state worth returning to.
  const backHref = safeReturnHref(query.returnTo, `/browse?user=${userId}`);
  // Started together: the persona name is shell chrome and must not stand in
  // front of the movie the route exists to show.
  const [state, personaName] = await Promise.all([
    detailForRequest(userId, parseMovieId(movieId)),
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
      <div className="app-page">
        <ResourceRegion state={state}>
          {(detail) => (
            <MovieDetailView
              backHref={backHref}
              item={detail.item}
              requestId={hasResourceData(state) ? state.requestId : ""}
              userId={userId}
            />
          )}
        </ResourceRegion>

        {hasResourceData(state) ? null : (
          <p className="movie-detail-escape">
            <Link className="button-secondary" href={backHref}>
              {returnHrefLabel(backHref)}
            </Link>
          </p>
        )}
      </div>
    </AppShell>
  );
}
