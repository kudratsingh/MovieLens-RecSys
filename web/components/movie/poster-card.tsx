"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

import { Icon } from "@/components/ui/icons";
import type { MovieCard as MovieCardType } from "@/lib/movie-types";
import "./poster-card.css";

export function PosterCard({
  movie,
  priority = false,
  density = "standard",
}: {
  movie: MovieCardType;
  priority?: boolean;
  density?: "standard" | "compact";
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const showFallback = !movie.posterSrc || imageFailed;
  const posterSrc = movie.posterSrc;

  return (
    <article className={`poster-card poster-card-${density}`}>
      <Link aria-label={`Open ${movie.title}`} className="poster-frame" href={`/ui-preview/movies/${movie.id}`}>
        {showFallback || !posterSrc ? (
          <PosterFallback movie={movie} />
        ) : (
          <Image
            alt={movie.posterAlt}
            fill
            onError={() => setImageFailed(true)}
            priority={priority}
            sizes="(max-width: 480px) 44vw, (max-width: 900px) 28vw, 220px"
            src={posterSrc}
          />
        )}
        {movie.rank ? <span className="rank-badge">#{movie.rank}</span> : null}
        <span className="poster-open-cue">
          Open <Icon name="arrow" />
        </span>
      </Link>
      <div className="poster-card-copy">
        <div>
          <Link className="poster-title" href={`/ui-preview/movies/${movie.id}`}>
            {movie.title}
          </Link>
          <p className="poster-meta">
            {movie.year ?? "Year unknown"}
            {movie.genres[0] ? ` · ${movie.genres[0]}` : " · Genre unavailable"}
          </p>
        </div>
        {movie.state.watchlisted ? (
          <span aria-label="In watchlist" className="poster-state" role="img">
            <Icon name="bookmark" />
          </span>
        ) : null}
      </div>
      {density === "standard" && movie.reason ? (
        <p className="poster-reason">{movie.reason}</p>
      ) : null}
    </article>
  );
}

function PosterFallback({ movie }: { movie: MovieCardType }) {
  const initials = movie.title
    .split(" ")
    .filter((word) => !["the", "a", "of", "in", "to"].includes(word.toLowerCase()))
    .slice(0, 2)
    .map((word) => word[0])
    .join("");

  return (
    <span className="poster-fallback" data-testid="poster-fallback">
      <span aria-hidden="true">{initials}</span>
      <span>Artwork unavailable</span>
    </span>
  );
}
