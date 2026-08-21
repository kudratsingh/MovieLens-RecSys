import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { auth } from "@/auth";
import { MovieDetailActions } from "@/components/movie-detail-actions";
import { MoviePoster } from "@/components/movie-poster";
import type { MovieDetailResponse } from "@/lib/api";
import { proxyRecommendationApi, validPositiveId } from "@/lib/backend";
import { requireApiAccessToken } from "@/lib/bff-auth";

type DetailProps = {
  params: Promise<{ movieId: string }>;
  searchParams: Promise<{ user?: string; returnTo?: string }>;
};

async function loadMovie(movieId: string, userId: number, accessToken: string) {
  if (!validPositiveId(movieId) || !Number.isSafeInteger(userId) || userId < 1) return null;
  const response = await proxyRecommendationApi(
    accessToken,
    `/users/${userId}/movies/${movieId}`,
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Movie API returned ${response.status}`);
  return (await response.json()) as MovieDetailResponse;
}

export async function generateMetadata({ params, searchParams }: DetailProps): Promise<Metadata> {
  const [{ movieId }, query, session] = await Promise.all([params, searchParams, auth()]);
  const userId = /^\d+$/.test(query.user ?? "") ? Number(query.user) : 900000101;
  const accessToken = requireApiAccessToken(session);
  const detail = accessToken
    ? await loadMovie(movieId, userId, accessToken).catch(() => null)
    : null;
  if (!detail) {
    return {
      title: "Movie unavailable | MovieLens",
      description: "This MovieLens catalog record is currently unavailable.",
      robots: { index: false },
      openGraph: { images: [] },
      twitter: { images: [] },
    };
  }
  const item = detail.item;
  const description = item.overview ?? `Explore ${item.title} in the MovieLens catalog.`;
  return {
    title: `${item.title} | MovieLens`,
    description,
    openGraph: { title: item.title, description, images: item.poster_url ? [item.poster_url] : [] },
    twitter: { card: "summary", title: item.title, description, images: item.poster_url ? [item.poster_url] : [] },
  };
}

export default async function MovieDetailPage({ params, searchParams }: DetailProps) {
  const [{ movieId }, query, session] = await Promise.all([params, searchParams, auth()]);
  if (!session?.user || session.error) redirect("/");
  const accessToken = requireApiAccessToken(session);
  if (!accessToken) redirect("/");
  const userId = /^\d+$/.test(query.user ?? "") ? Number(query.user) : 900000101;
  const detail = await loadMovie(movieId, userId, accessToken);
  if (!detail) notFound();
  const movie = detail.item;
  const backHref =
    query.returnTo === "/browse" || query.returnTo?.startsWith("/browse?")
    ? query.returnTo
    : `/browse?user=${userId}`;

  return (
    <main className="min-h-screen bg-[#08090b] text-zinc-100">
      <div className="mx-auto max-w-6xl px-5 pb-16 sm:px-8 lg:px-12">
        <header className="flex items-center justify-between border-b border-white/10 py-5">
          <Link className="flex items-center gap-3" href="/"><span className="grid size-9 place-items-center rounded-xl bg-amber-300 font-black text-zinc-950">M</span><span className="text-sm font-semibold">MovieLens</span></Link>
          <Link className="rounded-lg border border-white/10 px-4 py-2 text-sm text-zinc-300 transition hover:border-white/25 hover:text-white" href={backHref}>Back to Browse</Link>
        </header>

        <article className="grid gap-10 py-10 md:grid-cols-[minmax(240px,340px)_minmax(0,1fr)] md:py-16">
          <div className="group mx-auto w-full max-w-[340px] md:mx-0">
            <MoviePoster movieId={movie.movie_id} posterUrl={movie.poster_url} priority sizes="(max-width: 768px) 85vw, 340px" title={movie.title} />
          </div>
          <div className="self-center">
            <p className="font-mono text-xs uppercase tracking-[0.22em] text-amber-300">
              {movie.metadata_source} · {movie.source_status}
            </p>
            <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-[-0.045em] sm:text-6xl">{movie.title}</h1>
            <div className="mt-5 flex flex-wrap gap-2 text-sm text-zinc-400">
              <span>{movie.release_year ?? "Release year unavailable"}</span>
              {movie.genres.map((genre) => <span className="rounded-full border border-white/10 px-3 py-1" key={genre}>{genre}</span>)}
            </div>
            <p className="mt-7 max-w-2xl text-base leading-8 text-zinc-300">
              {movie.overview ?? "A synopsis is not available in the reviewed metadata snapshot yet."}
            </p>
            <div className="mt-6 flex gap-6 text-xs text-zinc-500">
              <span>{movie.interaction_count} demo interactions</span>
              <span>{movie.tmdb_id ? `TMDB ${movie.tmdb_id}` : "MovieLens metadata only"}</span>
            </div>
            <MovieDetailActions initialState={movie.state} movieId={movie.movie_id} title={movie.title} userId={userId} />
          </div>
        </article>
      </div>
    </main>
  );
}
