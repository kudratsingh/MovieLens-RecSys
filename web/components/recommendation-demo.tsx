"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Image from "next/image";

import type {
  PersonaItem,
  PersonaResponse,
  RecommendationItem,
  UserDashboard,
} from "@/lib/api";

export function RecommendationDemo() {
  const [userId, setUserId] = useState(1);
  const [inputValue, setInputValue] = useState("1");
  const [personas, setPersonas] = useState<PersonaItem[]>([]);
  const [dashboard, setDashboard] = useState<UserDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadUser = useCallback(async (nextUserId: number) => {
    try {
      const response = await fetch(`/api/users/${nextUserId}`, {
        cache: "no-store",
      });
      const payload = (await response.json()) as UserDashboard | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload && payload.detail
            ? payload.detail
            : "Recommendation API unavailable",
        );
      }
      setDashboard(payload as UserDashboard);
      setUserId(nextUserId);
    } catch (requestError) {
      setDashboard(null);
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Recommendation API unavailable",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    async function initializeDemo() {
      try {
        const response = await fetch("/api/personas", { cache: "no-store" });
        const payload = (await response.json()) as PersonaResponse | { detail?: string };
        if (!response.ok) {
          throw new Error(
            "detail" in payload && payload.detail
              ? payload.detail
              : "Persona API unavailable",
          );
        }
        const available = (payload as PersonaResponse).items;
        setPersonas(available);
        const initialUserId = available[0]?.user_id ?? 1;
        setInputValue(String(initialUserId));
        await loadUser(initialUserId);
      } catch (requestError) {
        setError(
          requestError instanceof Error ? requestError.message : "Persona API unavailable",
        );
        setLoading(false);
      }
    }
    queueMicrotask(() => void initializeDemo());
  }, [loadUser]);

  function selectPersona(nextUserId: number) {
    setInputValue(String(nextUserId));
    setLoading(true);
    setError(null);
    void loadUser(nextUserId);
  }

  function submitUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = Number.parseInt(inputValue, 10);
    if (!Number.isSafeInteger(parsed) || parsed < 1) {
      setError("Enter a positive numeric MovieLens user ID.");
      return;
    }
    setLoading(true);
    setError(null);
    void loadUser(parsed);
  }

  async function rateMovie(movieId: number, rating: number) {
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`/api/users/${userId}/ratings`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-csrf-token": await createCsrfToken(),
        },
        body: JSON.stringify({ movie_id: movieId, rating }),
      });
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "Could not save rating");
      await loadUser(userId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not save rating");
    } finally {
      setSaving(false);
    }
  }

  async function resetRatings() {
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`/api/users/${userId}/ratings`, {
        method: "DELETE",
        headers: { "x-csrf-token": await createCsrfToken() },
      });
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "Could not reset ratings");
      await loadUser(userId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not reset ratings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="border-t border-white/10 pt-8">
      <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">
            Demo identity
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {personas.map((persona) => (
              <button
                className={`rounded-full border px-4 py-2 text-sm transition ${
                  userId === persona.user_id
                    ? "border-amber-300 bg-amber-300 text-zinc-950"
                    : "border-white/10 bg-white/[0.035] text-zinc-300 hover:border-white/25"
                }`}
                key={persona.user_id}
                onClick={() => selectPersona(persona.user_id)}
                title={persona.description}
                type="button"
              >
                {persona.display_name}
              </button>
            ))}
          </div>
        </div>

        <form className="flex max-w-sm gap-2" onSubmit={submitUser}>
          <label className="sr-only" htmlFor="user-id">
            MovieLens user ID
          </label>
          <input
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.035] px-4 py-2.5 text-sm outline-none transition placeholder:text-zinc-600 focus:border-amber-300/70"
            id="user-id"
            inputMode="numeric"
            onChange={(event) => setInputValue(event.target.value)}
            placeholder="MovieLens user ID"
            value={inputValue}
          />
          <button
            className="rounded-xl bg-zinc-100 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-amber-300"
            type="submit"
          >
            Explore
          </button>
        </form>
      </div>

      {error ? (
        <ApiError
          message={error}
          onRetry={() => {
            setLoading(true);
            setError(null);
            void loadUser(userId);
          }}
        />
      ) : null}
      {loading ? <LoadingState /> : null}
      {!loading && dashboard ? (
        <Dashboard
          dashboard={dashboard}
          onRate={rateMovie}
          onReset={resetRatings}
          saving={saving}
        />
      ) : null}
    </section>
  );
}

async function createCsrfToken() {
  const response = await fetch("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not create a secure feedback request");
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}

function Dashboard({
  dashboard,
  onRate,
  onReset,
  saving,
}: {
  dashboard: UserDashboard;
  onRate: (movieId: number, rating: number) => Promise<void>;
  onReset: () => Promise<void>;
  saving: boolean;
}) {
  const { recommendations, history, catalog } = dashboard;
  return (
    <div className="mt-10 grid gap-12 xl:grid-cols-[minmax(0,1fr)_360px]">
      <RatingStudio
        items={catalog.items}
        onRate={onRate}
        onReset={onReset}
        saving={saving}
      />
      <div>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">
              Recommended for user {recommendations.user_id}
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">
              Your next watch
            </h2>
          </div>
          <div className="rounded-lg border border-white/10 px-3 py-2 font-mono text-xs text-zinc-400">
            {recommendations.model_version} · {recommendations.policy}
          </div>
        </div>

        {recommendations.items.length === 0 ? (
          <EmptyState label="No recommendations are available for this tenant yet." />
        ) : (
          <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {recommendations.items.map((movie, index) => (
              <article className="group" key={movie.movie_id}>
                <PosterArtwork movie={movie} rank={index + 1} />
                <p className="mt-3 text-xs leading-5 text-zinc-500">{movie.reason}</p>
              </article>
            ))}
          </div>
        )}
      </div>

      <aside>
        <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">Recent history</p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight">Taste signal</h2>
        {history.items.length === 0 ? (
          <EmptyState label="This is a cold-start user with no recent history." />
        ) : (
          <ol className="mt-6 divide-y divide-white/10 border-y border-white/10">
            {history.items.map((movie) => (
              <li className="flex gap-4 py-4" key={`${movie.movie_id}-${movie.timestamp}`}>
                <div className="grid size-10 shrink-0 place-items-center rounded-lg bg-white/[0.06] font-mono text-xs text-amber-300">
                  {movie.rating === null ? "✓" : movie.rating.toFixed(1)}
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{movie.title}</p>
                  <p className="mt-1 text-xs text-zinc-500">
                    {formatDate(movie.timestamp)} · {movie.genres[0] ?? "Unclassified"}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </aside>
      <div className="flex flex-wrap items-center gap-3 text-xs leading-5 text-zinc-600 xl:col-span-2">
        <a href="https://www.themoviedb.org" rel="noreferrer" target="_blank">
          <Image alt="TMDB" height={13} src="/tmdb-logo.svg" width={100} />
        </a>
        <p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
      </div>
    </div>
  );
}

function RatingStudio({
  items,
  onRate,
  onReset,
  saving,
}: {
  items: UserDashboard["catalog"]["items"];
  onRate: (movieId: number, rating: number) => Promise<void>;
  onReset: () => Promise<void>;
  saving: boolean;
}) {
  return (
    <section className="rounded-2xl border border-amber-300/20 bg-amber-300/[0.04] p-5 xl:col-span-2">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-amber-300">Interactive profile</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight">Rate movies, then watch the list react</h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-zinc-400">
            Pick 1–5 stars. Each rating is saved through tenant-scoped Postgres RLS and recommendations refresh immediately.
          </p>
        </div>
        <button
          className="rounded-lg border border-white/15 px-3 py-2 text-sm text-zinc-300 transition hover:border-red-300/40 hover:text-red-200 disabled:opacity-40"
          disabled={saving}
          onClick={() => void onReset()}
          type="button"
        >
          Clear ratings
        </button>
      </div>
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {items.slice(0, 18).map((movie) => (
          <article className="rounded-xl border border-white/10 bg-black/20 p-4" key={movie.movie_id}>
            <p className="truncate text-sm font-medium" title={movie.title}>{movie.title}</p>
            <p className="mt-1 truncate text-xs text-zinc-500">{movie.genres.join(" · ") || "Unclassified"}</p>
            <div className="mt-3 flex items-center gap-1" aria-label={`Rate ${movie.title}`}>
              {[1, 2, 3, 4, 5].map((rating) => (
                <button
                  aria-label={`${rating} stars`}
                  className={`grid size-8 place-items-center rounded-md text-sm transition ${
                    movie.rating === rating
                      ? "bg-amber-300 font-bold text-zinc-950"
                      : "bg-white/[0.06] text-zinc-400 hover:bg-white/[0.12] hover:text-amber-200"
                  }`}
                  disabled={saving}
                  key={rating}
                  onClick={() => void onRate(movie.movie_id, rating)}
                  type="button"
                >
                  {rating}
                </button>
              ))}
              <span className="ml-2 text-xs text-zinc-500">{movie.rating ? `${movie.rating.toFixed(1)}★` : "Unrated"}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function PosterArtwork({ movie, rank }: { movie: RecommendationItem; rank: number }) {
  const [imageFailed, setImageFailed] = useState(false);
  const showPoster = Boolean(movie.poster_url) && !imageFailed;

  return (
    <div
      className="relative aspect-[2/3] overflow-hidden rounded-xl border border-white/10 bg-zinc-900 p-4"
      style={
        showPoster
          ? undefined
          : {
              background: `linear-gradient(145deg, hsl(${(movie.movie_id * 47) % 360} 35% 24%), #111214 70%)`,
            }
      }
    >
      {showPoster && movie.poster_url ? (
        <Image
          alt=""
          className="object-cover transition duration-300 group-hover:scale-[1.02]"
          fill
          onError={() => setImageFailed(true)}
          sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 25vw"
          src={movie.poster_url}
        />
      ) : null}
      <div className="absolute inset-0 bg-gradient-to-t from-black via-black/10 to-black/25" />
      <span className="relative font-mono text-xs text-white/70">
        {String(rank).padStart(2, "0")}
      </span>
      <div className="absolute inset-x-4 bottom-4">
        <p className="text-lg font-semibold leading-tight drop-shadow">{movie.title}</p>
        <p className="mt-2 line-clamp-2 text-xs leading-5 text-white/70">
          {movie.release_year ? `${movie.release_year} · ` : ""}
          {movie.genres.join(" · ") || "Unclassified"}
        </p>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5" aria-label="Loading recommendations">
      {Array.from({ length: 5 }, (_, index) => (
        <div className="aspect-[2/3] animate-pulse rounded-xl bg-white/[0.055]" key={index} />
      ))}
    </div>
  );
}

function ApiError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mt-8 flex flex-col justify-between gap-4 rounded-xl border border-red-400/20 bg-red-400/[0.06] p-4 text-sm sm:flex-row sm:items-center">
      <div>
        <p className="font-medium text-red-200">The recommendation API is unavailable.</p>
        <p className="mt-1 text-red-200/60">{message}</p>
      </div>
      <button className="rounded-lg border border-red-200/20 px-3 py-2 text-red-100" onClick={onRetry} type="button">
        Retry
      </button>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <p className="mt-6 rounded-xl border border-dashed border-white/15 p-5 text-sm text-zinc-500">{label}</p>;
}

function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(timestamp * 1000));
}
