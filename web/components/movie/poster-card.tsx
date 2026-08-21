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
  href,
  metadataNote,
}: {
  movie: MovieCardType;
  priority?: boolean;
  density?: "standard" | "compact";
  /** Defaults to the recorded preview route; live routes pass their own. */
  href?: string;
  /** Source-aware metadata status, shown only when the record is incomplete. */
  metadataNote?: string | null;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const showFallback = !movie.posterSrc || imageFailed;
  const posterSrc = movie.posterSrc;
  const detailHref = href ?? `/ui-preview/movies/${movie.id}`;

  return (
    <article className={`poster-card poster-card-${density}`}>
      <Link aria-label={`Open ${movie.title}`} className="poster-frame" href={detailHref}>
        {showFallback || !posterSrc ? (
          <PosterFallbackMark title={movie.title} />
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
          <Link className="poster-title" href={detailHref}>
            {movie.title}
          </Link>
          <p className="poster-meta">
            {movie.year ?? "Year unknown"}
            {movie.genres[0] ? ` · ${movie.genres[0]}` : " · Genre unavailable"}
          </p>
          {metadataNote ? <p className="poster-metadata-note">{metadataNote}</p> : null}
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

/**
 * Derived from the title alone, so the same movie shows the same mark on every
 * render and in every screenshot. Exported because movie detail needs the
 * identical fallback: a poster that is missing in the grid must not look like
 * a different kind of gap one route later.
 */
export function posterInitials(title: string): string {
  return title
    .split(" ")
    .filter((word) => !["the", "a", "of", "in", "to"].includes(word.toLowerCase()))
    .slice(0, 2)
    .map((word) => word[0])
    .join("");
}

export function PosterFallbackMark({ title }: { title: string }) {
  return (
    <span className="poster-fallback" data-testid="poster-fallback">
      <span aria-hidden="true">{posterInitials(title)}</span>
      <span>Artwork unavailable</span>
    </span>
  );
}
