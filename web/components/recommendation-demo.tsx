"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import type { UserDashboard } from "@/lib/api";

const PERSONAS = [
  { label: "User 1", userId: 1 },
  { label: "User 42", userId: 42 },
  { label: "User 135", userId: 135 },
];

export function RecommendationDemo() {
  const [userId, setUserId] = useState(1);
  const [inputValue, setInputValue] = useState("1");
  const [dashboard, setDashboard] = useState<UserDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
    queueMicrotask(() => void loadUser(1));
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

  return (
    <section className="border-t border-white/10 pt-8">
      <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">
            Demo identity
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {PERSONAS.map((persona) => (
              <button
                className={`rounded-full border px-4 py-2 text-sm transition ${
                  userId === persona.userId
                    ? "border-amber-300 bg-amber-300 text-zinc-950"
                    : "border-white/10 bg-white/[0.035] text-zinc-300 hover:border-white/25"
                }`}
                key={persona.userId}
                onClick={() => selectPersona(persona.userId)}
                type="button"
              >
                {persona.label}
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
      {!loading && dashboard ? <Dashboard dashboard={dashboard} /> : null}
    </section>
  );
}

function Dashboard({ dashboard }: { dashboard: UserDashboard }) {
  const { recommendations, history } = dashboard;
  return (
    <div className="mt-10 grid gap-12 xl:grid-cols-[minmax(0,1fr)_360px]">
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
                <div
                  className="relative aspect-[2/3] overflow-hidden rounded-xl border border-white/10 p-4"
                  style={{
                    background: `linear-gradient(145deg, hsl(${(movie.movie_id * 47) % 360} 35% 24%), #111214 70%)`,
                  }}
                >
                  <span className="font-mono text-xs text-white/45">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className="absolute inset-x-4 bottom-4">
                    <p className="text-lg font-semibold leading-tight">{movie.title}</p>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-white/55">
                      {movie.genres.join(" · ") || "Unclassified"}
                    </p>
                  </div>
                </div>
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
                  {movie.rating.toFixed(1)}
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
