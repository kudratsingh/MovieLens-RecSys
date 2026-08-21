import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { cache } from "react";

import { auth } from "@/auth";
import { CatalogRouteHeader } from "@/components/browse/route-header";
import { MovieDetailView } from "@/components/movie/movie-detail-view";
import { ResourceRegion } from "@/components/ui/resource-region";
import { overviewText } from "@/lib/browse/catalog-card";
import { resolveDemoPersonaId, safeBrowseReturnHref } from "@/lib/demo-persona";
import { loadMovieDetail } from "@/lib/resources/server";
import { hasResourceData } from "@/lib/resources/state";

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
  const backHref = safeBrowseReturnHref(query.returnTo, `/browse?user=${userId}`);
  const state = await detailForRequest(userId, parseMovieId(movieId));

  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <CatalogRouteHeader actorName={actorName} current="movie" userId={userId} />
      <main className="app-page" id="main-content">
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
              Back to Browse
            </Link>
          </p>
        )}
      </main>
    </>
  );
}
