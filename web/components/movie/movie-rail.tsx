"use client";

import Link from "next/link";
import { useId, useRef } from "react";

import { PosterCard } from "@/components/movie/poster-card";
import { Icon } from "@/components/ui/icons";
import type { MovieCard } from "@/lib/movie-types";
import "./movie-rail.css";

export function MovieRail({
  title,
  eyebrow,
  movies,
  seeAllHref,
  movieHref,
  footer,
}: {
  title: string;
  eyebrow?: string;
  movies: readonly MovieCard[];
  seeAllHref: string;
  /** Live routes link into `/movies`; the preview keeps its own namespace. */
  movieHref?: (movie: MovieCard) => string;
  /** Optional per-card controls a live route needs and the preview does not. */
  footer?: (movie: MovieCard) => React.ReactNode;
}) {
  const railRef = useRef<HTMLDivElement>(null);
  const labelId = useId();

  function move(direction: -1 | 1) {
    railRef.current?.scrollBy({
      behavior: "smooth",
      left: direction * Math.max(railRef.current.clientWidth * 0.72, 260),
    });
  }

  return (
    <section className="movie-rail" aria-labelledby={labelId}>
      <header className="rail-header">
        <div>
          {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
          <h2 className="section-title" id={labelId}>
            {title}
          </h2>
        </div>
        <div className="rail-actions">
          <Link className="button-quiet" href={seeAllHref}>
            See all
          </Link>
          <button aria-label={`Scroll ${title} left`} className="icon-button" onClick={() => move(-1)} type="button">
            <Icon name="chevron-left" />
          </button>
          <button aria-label={`Scroll ${title} right`} className="icon-button" onClick={() => move(1)} type="button">
            <Icon name="chevron-right" />
          </button>
        </div>
      </header>
      <div
        aria-label={`${title} movies`}
        className="rail-track"
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") move(1);
          if (event.key === "ArrowLeft") move(-1);
        }}
        ref={railRef}
        tabIndex={0}
      >
        {movies.map((movie) => (
          <div className="rail-item" key={movie.id}>
            <PosterCard density="compact" href={movieHref?.(movie)} movie={movie} />
            {footer?.(movie)}
          </div>
        ))}
      </div>
    </section>
  );
}
