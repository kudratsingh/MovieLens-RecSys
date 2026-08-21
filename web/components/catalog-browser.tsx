"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { MoviePoster } from "@/components/movie-poster";
import type { CatalogItem, CatalogResponse } from "@/lib/api";

const GENRES = ["Action", "Comedy", "Drama", "Sci-Fi", "Thriller", "Animation"];
const DECADES = [
  { label: "Before 1980", from: "1878", to: "1979" },
  { label: "1980s", from: "1980", to: "1989" },
  { label: "1990s", from: "1990", to: "1999" },
  { label: "2000s", from: "2000", to: "2009" },
];

type SavedCatalogState = {
  items: CatalogItem[];
  nextCursor: string | null;
  hasMore: boolean;
  scrollY: number;
};

export function CatalogBrowser({ userId }: { userId: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const filtersKey = searchParams.toString();
  const load = useCallback(
    async (cursor?: string) => {
      const sequence = ++requestSequence.current;
      if (cursor) setLoadingMore(true);
      else {
        setLoading(true);
        setItems([]);
      }
      setError(null);
      const params = new URLSearchParams(filtersKey);
      params.set("limit", "24");
      if (cursor) params.set("cursor", cursor);
      try {
        const response = await fetch(
          `/api/users/${userId}/catalog?${params.toString()}`,
          { cache: "no-store" },
        );
        const payload = (await response.json()) as CatalogResponse | { detail?: string };
        if (!response.ok) {
          throw new Error(
            "detail" in payload && payload.detail
              ? payload.detail
              : "Catalog unavailable",
          );
        }
        if (sequence !== requestSequence.current) return;
        const catalog = payload as CatalogResponse;
        setItems((current) => (cursor ? [...current, ...catalog.items] : catalog.items));
        setNextCursor(catalog.page.next_cursor);
        setHasMore(catalog.page.has_more);
      } catch (requestError) {
        if (sequence !== requestSequence.current) return;
        setError(
          requestError instanceof Error ? requestError.message : "Catalog unavailable",
        );
      } finally {
        if (sequence === requestSequence.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [filtersKey, userId],
  );

  useEffect(() => {
    queueMicrotask(() => {
      const key = `browse-state:${filtersKey}`;
      setQuery(new URLSearchParams(filtersKey).get("q") ?? "");
      const raw = sessionStorage.getItem(key);
      sessionStorage.removeItem(key);
      if (raw) {
        try {
          const saved = JSON.parse(raw) as SavedCatalogState;
          if (
            Array.isArray(saved.items) &&
            typeof saved.hasMore === "boolean" &&
            (saved.nextCursor === null || typeof saved.nextCursor === "string") &&
            typeof saved.scrollY === "number"
          ) {
            requestSequence.current += 1;
            setItems(saved.items);
            setNextCursor(saved.nextCursor);
            setHasMore(saved.hasMore);
            setLoading(false);
            requestAnimationFrame(() =>
              window.scrollTo({ top: saved.scrollY, behavior: "instant" }),
            );
            return;
          }
        } catch {
          // A malformed device-local restoration entry is safe to ignore.
        }
      }
      void load();
    });
  }, [filtersKey, load]);

  function updateFilters(updates: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [name, value] of Object.entries(updates)) {
      if (value) params.set(name, value);
      else params.delete(name);
    }
    router.replace(`${pathname}${params.size ? `?${params.toString()}` : ""}`, {
      scroll: false,
    });
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    updateFilters({ q: query.trim() || null });
  }

  const activeGenre = searchParams.get("genre");
  const activeYearFrom = searchParams.get("year_from");
  const activeYearTo = searchParams.get("year_to");
  const activeSort = searchParams.get("sort") ?? "title";
  const hasFilters = Boolean(
    searchParams.get("q") ||
      activeGenre ||
      activeYearFrom ||
      activeYearTo ||
      activeSort !== "title",
  );

  return (
    <section aria-labelledby="browse-heading">
      <div className="border-b border-white/10 pb-8 pt-12 sm:pt-16">
        <p className="font-mono text-xs uppercase tracking-[0.24em] text-amber-300">
          120-title reviewed demo catalog
        </p>
        <div className="mt-4 flex flex-col justify-between gap-6 lg:flex-row lg:items-end">
          <div>
            <h1 id="browse-heading" className="text-4xl font-semibold tracking-[-0.04em] sm:text-6xl">
              Find the next one.
            </h1>
            <p className="mt-4 max-w-2xl text-base leading-7 text-zinc-400">
              Search a local metadata snapshot. Posters and details never wait on a live
              third-party request.
            </p>
          </div>
          <form className="flex w-full max-w-xl gap-2" onSubmit={submitSearch} role="search">
            <label className="sr-only" htmlFor="catalog-search">Search movies</label>
            <input
              className="min-w-0 flex-1 rounded-xl border border-white/15 bg-white/[0.04] px-4 py-3 text-sm outline-none transition placeholder:text-zinc-600 focus:border-amber-300 focus:ring-2 focus:ring-amber-300/20"
              id="catalog-search"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search titles"
              value={query}
            />
            <button className="rounded-xl bg-amber-300 px-5 py-3 text-sm font-semibold text-zinc-950 transition hover:bg-amber-200" type="submit">
              Search
            </button>
          </form>
        </div>

        <div className="mt-7 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex gap-2 overflow-x-auto pb-2" aria-label="Filter by genre">
            {GENRES.map((genre) => (
              <button
                aria-pressed={activeGenre === genre}
                className={`shrink-0 rounded-full border px-4 py-2 text-sm transition ${
                  activeGenre === genre
                    ? "border-amber-300 bg-amber-300 text-zinc-950"
                    : "border-white/10 text-zinc-400 hover:border-white/25 hover:text-white"
                }`}
                key={genre}
                onClick={() => updateFilters({ genre: activeGenre === genre ? null : genre })}
                type="button"
              >
                {genre}
              </button>
            ))}
          </div>
          <label className="flex shrink-0 items-center gap-3 text-sm text-zinc-500">
            Sort
            <select
              className="rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-zinc-200 outline-none focus:border-amber-300"
              onChange={(event) => updateFilters({ sort: event.target.value === "title" ? null : event.target.value })}
              value={activeSort}
            >
              <option value="title">Title</option>
              <option value="newest">Newest</option>
              <option value="popular">Popular here</option>
            </select>
          </label>
        </div>

        <div className="mt-2 flex gap-2 overflow-x-auto pb-2" aria-label="Filter by decade">
          {DECADES.map((decade) => {
            const active = activeYearFrom === decade.from && activeYearTo === decade.to;
            return (
              <button
                aria-pressed={active}
                className={`shrink-0 rounded-full border px-4 py-2 text-xs transition ${
                  active
                    ? "border-sky-300 bg-sky-300 text-zinc-950"
                    : "border-white/10 text-zinc-500 hover:border-white/25 hover:text-white"
                }`}
                key={decade.label}
                onClick={() =>
                  updateFilters({
                    year_from: active ? null : decade.from,
                    year_to: active ? null : decade.to,
                  })
                }
                type="button"
              >
                {decade.label}
              </button>
            );
          })}
        </div>

        {hasFilters ? (
          <button className="mt-3 text-sm text-amber-200 underline decoration-amber-300/30 underline-offset-4" onClick={() => { setQuery(""); router.replace(pathname, { scroll: false }); }} type="button">
            Clear active filters
          </button>
        ) : null}
      </div>

      {error ? (
        <div className="my-8 flex items-center justify-between gap-4 rounded-xl border border-red-300/20 bg-red-300/[0.06] p-4 text-sm text-red-100" role="alert">
          <span>{error}</span>
          <button className="rounded-lg border border-red-200/30 px-3 py-2" onClick={() => void load()} type="button">Retry</button>
        </div>
      ) : null}

      {loading ? <CatalogSkeleton /> : null}
      {!loading && !error && items.length === 0 ? (
        <div className="my-16 rounded-2xl border border-dashed border-white/15 p-10 text-center">
          <h2 className="text-xl font-semibold">No movies match those filters.</h2>
          <p className="mt-2 text-sm text-zinc-500">Clear a filter or try a broader title search.</p>
        </div>
      ) : null}

      {!loading && items.length ? (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-8 py-9 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            {items.map((movie) => (
              <Link
                className="group rounded-[1.15rem] outline-none focus-visible:ring-2 focus-visible:ring-amber-300 focus-visible:ring-offset-4 focus-visible:ring-offset-[#08090b]"
                href={{
                  pathname: `/movies/${movie.movie_id}`,
                  query: {
                    user: String(userId),
                    returnTo: `${pathname}${filtersKey ? `?${filtersKey}` : ""}`,
                  },
                }}
                key={movie.movie_id}
                onClick={() =>
                  sessionStorage.setItem(
                    `browse-state:${filtersKey}`,
                    JSON.stringify({
                      items,
                      nextCursor,
                      hasMore,
                      scrollY: window.scrollY,
                    } satisfies SavedCatalogState),
                  )
                }
              >
                <MoviePoster movieId={movie.movie_id} posterUrl={movie.poster_url} sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 17vw" title={movie.title} />
                <h2 className="mt-3 line-clamp-1 text-sm font-medium text-zinc-100">{movie.title}</h2>
                <p className="mt-1 flex items-center gap-2 text-xs text-zinc-500">
                  <span>{movie.release_year ?? "Year unavailable"}</span>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">{movie.genres[0] ?? "Unclassified"}</span>
                </p>
                {movie.state?.rating ? <p className="mt-2 text-xs font-medium text-amber-300">Rated {movie.state.rating.toFixed(1)} ★</p> : null}
              </Link>
            ))}
          </div>
          {hasMore && nextCursor ? (
            <div className="flex justify-center pb-16">
              <button
                className="min-w-44 rounded-xl border border-white/15 px-6 py-3 text-sm font-semibold transition hover:border-amber-300/60 hover:text-amber-200 disabled:cursor-wait disabled:opacity-50"
                disabled={loadingMore}
                onClick={() => void load(nextCursor)}
                type="button"
              >
                {loadingMore ? "Loading…" : "Load more movies"}
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function CatalogSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-8 py-9 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6" aria-label="Loading catalog">
      {Array.from({ length: 12 }, (_, index) => (
        <div key={index}>
          <div className="aspect-[2/3] animate-pulse rounded-[1.15rem] bg-white/[0.055]" />
          <div className="mt-3 h-4 w-4/5 animate-pulse rounded bg-white/[0.055]" />
        </div>
      ))}
    </div>
  );
}
