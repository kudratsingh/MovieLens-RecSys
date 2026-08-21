"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { SignOutButton } from "@/components/auth-controls";
import type {
  FeedbackMutationResponse,
  LibraryMovie,
  LibraryResponse,
  MovieState,
  PersonaResponse,
  TasteSummaryResponse,
} from "@/lib/api";

type LibraryTab = "rated" | "watchlist" | "history";
type LibrarySort = "recent" | "title" | "rating";
type FeedbackResource = "watched" | "rating" | "watchlist" | "dismissal";

const TAB_COPY: Record<LibraryTab, { label: string; empty: string }> = {
  rated: {
    label: "Rated",
    empty: "No ratings match this view yet. Rate a watched movie to start this collection.",
  },
  watchlist: {
    label: "Watchlist",
    empty: "Nothing is saved here yet. Add an unwatched movie when you want to return to it.",
  },
  history: {
    label: "History",
    empty: "No watched movies match this view yet.",
  },
};

export function LibraryExperience({
  actorName,
  userId,
  initialTab,
  initialSort,
  initialQuery,
}: {
  actorName: string;
  userId: number;
  initialTab: LibraryTab;
  initialSort: LibrarySort;
  initialQuery: string;
}) {
  const router = useRouter();
  const [tab, setTab] = useState<LibraryTab>(initialTab);
  const [sort, setSort] = useState<LibrarySort>(
    initialSort === "rating" && initialTab !== "rated" ? "recent" : initialSort,
  );
  const [query, setQuery] = useState(initialQuery);
  const [queryDraft, setQueryDraft] = useState(initialQuery);
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [taste, setTaste] = useState<TasteSummaryResponse | null>(null);
  const [personaName, setPersonaName] = useState(`Persona ${userId}`);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyMovieId, setBusyMovieId] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const fetchLibrary = useCallback(
    async (cursor?: string, append = false) => {
      if (append) setLoadingMore(true);
      else setLoading(true);
      setError(null);
      const parameters = new URLSearchParams({
        tab,
        sort,
        limit: "18",
      });
      if (query) parameters.set("q", query);
      if (cursor) parameters.set("cursor", cursor);
      try {
        const response = await fetch(`/api/users/${userId}/library?${parameters}`, {
          cache: "no-store",
        });
        const payload = (await response.json()) as LibraryResponse | { detail?: string };
        if (!response.ok || !isLibraryResponse(payload)) {
          throw new Error(
            "detail" in payload && payload.detail
              ? payload.detail
              : "The selected persona's library is unavailable.",
          );
        }
        setLibrary((current) =>
          append && current
            ? { ...payload, items: [...current.items, ...payload.items] }
            : payload,
        );
      } catch (requestError) {
        if (!append) setLibrary(null);
        setError(
          requestError instanceof Error
            ? requestError.message
            : "The selected persona's library is unavailable.",
        );
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [query, sort, tab, userId],
  );

  useEffect(() => {
    queueMicrotask(() => void fetchLibrary());
  }, [fetchLibrary]);

  useEffect(() => {
    async function loadSupportingContext() {
      const [personasResponse, tasteResponse] = await Promise.all([
        fetch("/api/personas", { cache: "no-store" }),
        fetch(`/api/users/${userId}/taste-profile`, { cache: "no-store" }),
      ]);
      if (personasResponse.ok) {
        const payload = (await personasResponse.json()) as PersonaResponse;
        const selected = payload.items.find((item) => item.user_id === userId);
        if (selected) setPersonaName(selected.display_name);
      }
      if (tasteResponse.ok) {
        const payload = (await tasteResponse.json()) as TasteSummaryResponse;
        if (payload.source === "live-ratings-v1") setTaste(payload);
      }
    }
    queueMicrotask(() => void loadSupportingContext());
  }, [userId]);

  function updateUrl(nextTab: LibraryTab, nextSort: LibrarySort, nextQuery: string) {
    const parameters = new URLSearchParams({ userId: String(userId), tab: nextTab });
    if (nextSort !== "recent") parameters.set("sort", nextSort);
    if (nextQuery) parameters.set("q", nextQuery);
    router.replace(`/library?${parameters}`, { scroll: false });
  }

  function selectTab(nextTab: LibraryTab) {
    const nextSort = sort === "rating" && nextTab !== "rated" ? "recent" : sort;
    setTab(nextTab);
    setSort(nextSort);
    updateUrl(nextTab, nextSort, query);
  }

  function selectSort(nextSort: LibrarySort) {
    setSort(nextSort);
    updateUrl(tab, nextSort, query);
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextQuery = queryDraft.trim();
    setQuery(nextQuery);
    updateUrl(tab, sort, nextQuery);
  }

  async function mutate(
    movie: LibraryMovie,
    resource: FeedbackResource,
    method: "PUT" | "DELETE",
    rating?: number,
    focusId?: string,
  ) {
    const previous = library;
    setBusyMovieId(movie.movie_id);
    setError(null);
    setLibrary((current) => optimisticLibrary(current, movie.movie_id, resource, method, rating));
    try {
      const csrf = await csrfToken();
      const response = await fetch(
        `/api/users/${userId}/movies/${movie.movie_id}/${resource}` +
          `?expected_revision=${movie.state.revision}`,
        {
          method,
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
            "x-csrf-token": csrf,
          },
          body: resource === "rating" && method === "PUT" ? JSON.stringify({ rating }) : undefined,
        },
      );
      const payload = (await response.json()) as FeedbackMutationResponse | { detail?: string };
      if (!response.ok || !isMutationResponse(payload)) {
        throw new Error(
          "detail" in payload && payload.detail ? payload.detail : "The movie state was not saved.",
        );
      }
      setLibrary((current) => reconcileCanonical(current, payload.state));
      setAnnouncement(mutationAnnouncement(resource, method, movie.title));
      await fetchLibrary();
      const tasteResponse = await fetch(`/api/users/${userId}/taste-profile`, {
        cache: "no-store",
      });
      if (tasteResponse.ok) setTaste((await tasteResponse.json()) as TasteSummaryResponse);
    } catch (requestError) {
      setLibrary(previous);
      setError(
        requestError instanceof Error ? requestError.message : "The movie state was not saved.",
      );
      setAnnouncement(`Could not update ${movie.title}. The previous state was restored.`);
    } finally {
      setBusyMovieId(null);
      requestAnimationFrame(() => {
        const target = focusId ? document.getElementById(focusId) : null;
        (target ?? document.getElementById(`tab-${tab}`) ?? document.getElementById("library-title"))
          ?.focus();
      });
    }
  }

  const counts = library?.counts ?? { rated: 0, watchlist: 0, history: 0 };
  const validSorts = useMemo(
    () => (tab === "rated" ? (["recent", "title", "rating"] as const) : (["recent", "title"] as const)),
    [tab],
  );

  return (
    <main className="min-h-screen bg-[#08090b] text-zinc-100">
      <div className="mx-auto max-w-6xl px-5 pb-20 sm:px-8">
        <header className="flex items-center justify-between border-b border-white/10 py-5">
          <Link className="flex items-center gap-3" href="/">
            <span className="grid size-9 place-items-center rounded-xl bg-amber-300 font-black text-zinc-950">
              M
            </span>
            <span className="text-sm font-semibold">MovieLens</span>
          </Link>
          <div className="flex items-center gap-5">
            <nav aria-label="Primary" className="flex items-center gap-5 text-sm text-zinc-400">
              <Link className="transition hover:text-white" href="/">For you</Link>
              <span aria-current="page" className="text-amber-200">Library</span>
            </nav>
            <span className="hidden text-xs text-zinc-500 md:inline" title={actorName}>
              Signed in as {actorName}
            </span>
            <SignOutButton />
          </div>
        </header>

        <section className="pb-8 pt-12">
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-amber-300">
            Exploring as {personaName}
          </p>
          <h1
            className="mt-3 max-w-3xl text-4xl font-semibold tracking-[-0.04em] sm:text-6xl"
            id="library-title"
            tabIndex={-1}
          >
            Movies you have already put in motion.
          </h1>
          <p className="mt-5 max-w-2xl leading-7 text-zinc-400">
            This is the selected demo persona&apos;s live state—not the signed-in actor&apos;s private library.
          </p>
        </section>

        <section aria-labelledby="collection-title">
          <div className="flex gap-1 overflow-x-auto border-b border-white/10" role="tablist">
            {(Object.keys(TAB_COPY) as LibraryTab[]).map((item) => (
              <button
                aria-selected={tab === item}
                className={`min-w-max border-b-2 px-4 py-3 text-sm font-medium transition ${
                  tab === item
                    ? "border-amber-300 text-white"
                    : "border-transparent text-zinc-500 hover:text-zinc-200"
                }`}
                id={`tab-${item}`}
                key={item}
                onClick={() => selectTab(item)}
                role="tab"
                type="button"
              >
                {TAB_COPY[item].label} <span className="ml-1 text-zinc-600">{counts[item]}</span>
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-3 py-6 sm:flex-row sm:items-center sm:justify-between">
            <form className="flex max-w-lg flex-1 gap-2" onSubmit={submitSearch}>
              <label className="sr-only" htmlFor="library-search">Filter this collection</label>
              <input
                className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-2.5 outline-none transition placeholder:text-zinc-600 focus:border-amber-300"
                id="library-search"
                onChange={(event) => setQueryDraft(event.target.value)}
                placeholder={`Filter ${TAB_COPY[tab].label.toLowerCase()} by title`}
                value={queryDraft}
              />
              <button className="rounded-xl bg-zinc-100 px-4 text-sm font-semibold text-zinc-950 hover:bg-amber-300" type="submit">
                Filter
              </button>
            </form>
            <label className="flex items-center gap-3 text-sm text-zinc-500">
              Sort
              <select
                className="rounded-xl border border-white/10 bg-zinc-900 px-3 py-2.5 text-zinc-200 outline-none focus:border-amber-300"
                onChange={(event) => selectSort(event.target.value as LibrarySort)}
                value={sort}
              >
                {validSorts.map((item) => (
                  <option key={item} value={item}>
                    {item === "recent" ? "Most recent" : item === "rating" ? "Highest rated" : "Title"}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <h2 className="sr-only" id="collection-title">{TAB_COPY[tab].label}</h2>
          <p aria-live="polite" className="sr-only">{announcement}</p>
          {error ? (
            <div className="mb-5 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-red-300/20 bg-red-300/[0.05] p-4 text-sm text-red-100" role="alert">
              <span>{error}</span>
              <button className="underline underline-offset-4" onClick={() => void fetchLibrary()} type="button">Try again</button>
            </div>
          ) : null}
          {loading && !library ? <LibrarySkeleton /> : null}
          {!loading && library?.items.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-white/15 px-6 py-16 text-center text-zinc-400">
              <p className="text-lg text-zinc-200">This collection is quiet.</p>
              <p className="mx-auto mt-2 max-w-lg text-sm leading-6">{TAB_COPY[tab].empty}</p>
            </div>
          ) : null}
          {library?.items.length ? (
            <div className="divide-y divide-white/10 border-y border-white/10">
              {library.items.map((movie) => (
                <LibraryRow
                  busy={busyMovieId === movie.movie_id}
                  key={movie.movie_id}
                  movie={movie}
                  mutate={mutate}
                  tab={tab}
                />
              ))}
            </div>
          ) : null}
          {library?.page.has_more && library.page.next_cursor ? (
            <div className="pt-7 text-center">
              <button
                className="rounded-xl border border-white/15 px-5 py-3 text-sm font-semibold transition hover:border-amber-300/60 hover:text-amber-200 disabled:opacity-40"
                disabled={loadingMore}
                onClick={() => void fetchLibrary(library.page.next_cursor ?? undefined, true)}
                type="button"
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          ) : null}
        </section>

        {taste ? <TasteSummary summary={taste} /> : null}
      </div>
    </main>
  );
}

function LibraryRow({
  movie,
  tab,
  busy,
  mutate,
}: {
  movie: LibraryMovie;
  tab: LibraryTab;
  busy: boolean;
  mutate: (
    movie: LibraryMovie,
    resource: FeedbackResource,
    method: "PUT" | "DELETE",
    rating?: number,
    focusId?: string,
  ) => Promise<void>;
}) {
  const state = movie.state;
  const anchorId = `movie-${movie.movie_id}-primary`;
  return (
    <article className="grid gap-4 py-5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
      <div className="min-w-0">
        <div className="flex items-start gap-4">
          <div
            aria-hidden="true"
            className="grid aspect-[2/3] w-12 shrink-0 place-items-center rounded-md bg-gradient-to-br from-amber-300/20 to-zinc-900 font-mono text-xs text-zinc-500"
          >
            {movie.movie_id}
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold" title={movie.title}>{movie.title}</h3>
            <p className="mt-1 truncate text-xs text-zinc-500">{movie.genres.join(" · ") || "Unclassified"}</p>
            <p className="mt-2 text-xs text-zinc-500">
              {tab === "watchlist"
                ? `Saved ${formatDate(state.watchlisted_at)}`
                : `Watched ${formatDate(state.watched_at)}`}
              {state.dismissed_at ? " · Not for this persona" : ""}
            </p>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 md:justify-end">
        {state.watched_at ? (
          <RatingControl busy={busy} movie={movie} mutate={mutate} />
        ) : (
          <button
            className="rounded-lg bg-amber-300 px-3 py-2 text-xs font-semibold text-zinc-950 disabled:opacity-40"
            disabled={busy}
            id={anchorId}
            onClick={() => void mutate(movie, "watched", "PUT", undefined, anchorId)}
            type="button"
          >
            Mark watched
          </button>
        )}
        {state.watchlisted_at ? (
          <button
            className="rounded-lg border border-white/15 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40"
            disabled={busy}
            id={anchorId}
            onClick={() => void mutate(movie, "watchlist", "DELETE", undefined, anchorId)}
            type="button"
          >
            Remove saved
          </button>
        ) : null}
        {state.dismissed_at ? (
          <button
            className="rounded-lg border border-white/15 px-3 py-2 text-xs text-zinc-300 disabled:opacity-40"
            disabled={busy}
            id={anchorId}
            onClick={() => void mutate(movie, "dismissal", "DELETE", undefined, anchorId)}
            type="button"
          >
            Undo not for me
          </button>
        ) : (
          <button
            className="rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-500 transition hover:border-white/25 hover:text-zinc-200 disabled:opacity-40"
            disabled={busy}
            onClick={() => void mutate(movie, "dismissal", "PUT", undefined, anchorId)}
            type="button"
          >
            Not for me
          </button>
        )}
        {tab === "history" ? (
          <button
            className="rounded-lg px-2 py-2 text-xs text-red-300/70 hover:text-red-200 disabled:opacity-40"
            disabled={busy}
            onClick={() => {
              if (window.confirm(`Remove ${movie.title} from watched history? This also removes its rating.`)) {
                void mutate(movie, "watched", "DELETE", undefined, anchorId);
              }
            }}
            type="button"
          >
            Remove history
          </button>
        ) : null}
      </div>
    </article>
  );
}

function RatingControl({
  movie,
  busy,
  mutate,
}: {
  movie: LibraryMovie;
  busy: boolean;
  mutate: LibraryRowParameters["mutate"];
}) {
  const id = `movie-${movie.movie_id}-rating`;
  return (
    <div aria-label={`Rating for ${movie.title}`} className="flex items-center gap-1" role="group">
      <label className="sr-only" htmlFor={id}>Rating for {movie.title}</label>
      <select
        className="rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-xs text-zinc-200 outline-none focus:border-amber-300 disabled:opacity-40"
        disabled={busy}
        id={id}
        onChange={(event) => {
          const rating = Number(event.target.value);
          if (rating) void mutate(movie, "rating", "PUT", rating, id);
        }}
        value={movie.state.rating ?? ""}
      >
        <option disabled value="">Rate</option>
        {Array.from({ length: 10 }, (_, index) => (index + 1) / 2).map((rating) => (
          <option key={rating} value={rating}>{rating.toFixed(1)} stars</option>
        ))}
      </select>
      {movie.state.rating !== null ? (
        <button
          className="ml-1 px-2 py-2 text-xs text-zinc-500 hover:text-red-200 disabled:opacity-40"
          disabled={busy}
          onClick={() => void mutate(movie, "rating", "DELETE", undefined, id)}
          type="button"
        >
          Clear
        </button>
      ) : null}
    </div>
  );
}

type LibraryRowParameters = Parameters<typeof LibraryRow>[0];

function TasteSummary({ summary }: { summary: TasteSummaryResponse }) {
  return (
    <aside className="mt-14 border-t border-white/10 pt-8">
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-lg">
          <p className="font-mono text-xs uppercase tracking-[0.2em] text-zinc-500">Live ratings summary</p>
          <h2 className="mt-2 text-2xl font-semibold">A readable outline of this persona&apos;s ratings.</h2>
          <p className="mt-3 text-sm leading-6 text-zinc-500">{summary.explanation}</p>
        </div>
        <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-2 lg:max-w-xl">
          {summary.top_genres.map((genre) => (
            <div className="flex items-center justify-between rounded-xl border border-white/10 px-4 py-3 text-sm" key={genre.genre}>
              <span>{genre.genre}</span>
              <span className="font-mono text-xs text-zinc-500">{genre.rated_count} rated · {genre.average_rating.toFixed(1)} avg</span>
            </div>
          ))}
          {!summary.top_genres.length ? <p className="text-sm text-zinc-500">Rate a movie to reveal this summary.</p> : null}
        </div>
      </div>
    </aside>
  );
}

function LibrarySkeleton() {
  return (
    <div aria-label="Loading library" className="divide-y divide-white/10 border-y border-white/10" role="status">
      {[1, 2, 3].map((item) => (
        <div className="flex items-center gap-4 py-5" key={item}>
          <div className="aspect-[2/3] w-12 animate-pulse rounded-md bg-white/[0.06]" />
          <div className="flex-1 space-y-2"><div className="h-4 w-1/3 animate-pulse rounded bg-white/[0.06]" /><div className="h-3 w-1/4 animate-pulse rounded bg-white/[0.04]" /></div>
        </div>
      ))}
    </div>
  );
}

function optimisticLibrary(
  current: LibraryResponse | null,
  movieId: number,
  resource: FeedbackResource,
  method: "PUT" | "DELETE",
  rating?: number,
): LibraryResponse | null {
  if (!current) return current;
  return {
    ...current,
    items: current.items.map((movie) => {
      if (movie.movie_id !== movieId) return movie;
      const now = new Date().toISOString();
      let state = movie.state;
      if (resource === "rating") {
        state = method === "PUT"
          ? { ...state, rating: rating ?? null, watched_at: state.watched_at ?? now, rating_updated_at: now, watchlisted_at: null }
          : { ...state, rating: null, rating_updated_at: null };
      } else if (resource === "watched") {
        state = method === "PUT"
          ? { ...state, watched_at: state.watched_at ?? now, watchlisted_at: null }
          : { ...state, watched_at: null, rating: null, rating_updated_at: null };
      } else if (resource === "watchlist") {
        state = { ...state, watchlisted_at: method === "PUT" ? state.watchlisted_at ?? now : null };
      } else {
        state = { ...state, dismissed_at: method === "PUT" ? state.dismissed_at ?? now : null, watchlisted_at: method === "PUT" ? null : state.watchlisted_at };
      }
      return { ...movie, state };
    }),
  };
}

function reconcileCanonical(current: LibraryResponse | null, state: MovieState): LibraryResponse | null {
  if (!current) return current;
  return {
    ...current,
    items: current.items.map((movie) =>
      movie.movie_id === state.movie_id ? { ...movie, state } : movie,
    ),
  };
}

function mutationAnnouncement(
  resource: FeedbackResource,
  method: "PUT" | "DELETE",
  title: string,
): string {
  if (resource === "rating") return method === "PUT" ? `Rating saved for ${title}.` : `Rating removed from ${title}; watched history was preserved.`;
  if (resource === "watched") return method === "PUT" ? `${title} marked watched.` : `${title} removed from watched history.`;
  if (resource === "watchlist") return method === "PUT" ? `${title} saved to watchlist.` : `${title} removed from watchlist.`;
  return method === "PUT" ? `${title} will be excluded from recommendations.` : `${title} is eligible again.`;
}

function isLibraryResponse(value: unknown): value is LibraryResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<LibraryResponse>;
  return Array.isArray(candidate.items) && Boolean(candidate.counts) && Boolean(candidate.page);
}

function isMutationResponse(value: unknown): value is FeedbackMutationResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<FeedbackMutationResponse>;
  return typeof candidate.request_id === "string" && typeof candidate.state?.revision === "number";
}

function formatDate(value: string | null): string {
  if (!value) return "date unavailable";
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

async function csrfToken(): Promise<string> {
  const response = await fetch("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not create a secure feedback request");
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}
