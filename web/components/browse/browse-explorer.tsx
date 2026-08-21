"use client";

import { useMemo, useState } from "react";

import { MovieCollection } from "@/components/movie/movie-collection";
import { Drawer } from "@/components/ui/drawer";
import { EmptyState } from "@/components/ui/resource-states";
import { Icon } from "@/components/ui/icons";
import type { MovieCard } from "@/lib/movie-types";
import "./browse-explorer.css";

const genres = ["Drama", "Thriller", "Mystery", "Romance", "Animation"];

export function BrowseExplorer({ movies }: { movies: readonly MovieCard[] }) {
  const [query, setQuery] = useState("");
  const [activeGenres, setActiveGenres] = useState<string[]>([]);
  const [sort, setSort] = useState("rank");

  const visibleMovies = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const filtered = movies.filter(
      (movie) =>
        (!normalized || movie.title.toLowerCase().includes(normalized)) &&
        (!activeGenres.length || activeGenres.some((genre) => movie.genres.includes(genre))),
    );
    return [...filtered].sort((a, b) => {
      if (sort === "title") return a.title.localeCompare(b.title);
      if (sort === "year") return (b.year ?? 0) - (a.year ?? 0);
      return (a.rank ?? 999) - (b.rank ?? 999);
    });
  }, [activeGenres, movies, query, sort]);

  function toggleGenre(genre: string) {
    setActiveGenres((current) =>
      current.includes(genre) ? current.filter((item) => item !== genre) : [...current, genre],
    );
  }

  return (
    <>
      <div className="browse-toolbar">
        <label className="search-field">
          <span className="visually-hidden">Search movies</span>
          <Icon name="search" />
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search titles"
            type="search"
            value={query}
          />
        </label>
        <Drawer buttonClassName="button-secondary" buttonLabel="Filters" eyebrow="Narrow the catalog" title="Filters">
          <fieldset className="filter-options">
            <legend>Genre</legend>
            {genres.map((genre) => (
              <label key={genre}>
                <input
                  checked={activeGenres.includes(genre)}
                  onChange={() => toggleGenre(genre)}
                  type="checkbox"
                />
                <span>{genre}</span>
              </label>
            ))}
          </fieldset>
          <button className="button-secondary mt-6" onClick={() => setActiveGenres([])} type="button">
            Clear filters
          </button>
        </Drawer>
      </div>

      {activeGenres.length ? (
        <div aria-label="Active filters" className="active-filters">
          {activeGenres.map((genre) => (
            <button key={genre} onClick={() => toggleGenre(genre)} type="button">
              {genre} <span aria-hidden="true">×</span>
              <span className="visually-hidden">Remove filter</span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="collection-bar">
        <p aria-live="polite">
          <strong>{visibleMovies.length}</strong> movies
          <span> · recorded catalog fixture</span>
        </p>
        <label>
          <span>Sort</span>
          <select onChange={(event) => setSort(event.target.value)} value={sort}>
            <option value="rank">Curated order</option>
            <option value="title">Title A–Z</option>
            <option value="year">Newest year</option>
          </select>
        </label>
      </div>

      {visibleMovies.length ? (
        <MovieCollection label="Browse results" movies={visibleMovies} />
      ) : (
        <EmptyState
          action={<button className="button-secondary" onClick={() => { setQuery(""); setActiveGenres([]); }} type="button">Clear search and filters</button>}
          message="Try a different title or remove a filter."
          title="No movies match this cut"
        />
      )}
    </>
  );
}
