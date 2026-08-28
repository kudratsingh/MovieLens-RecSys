"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

import { Icon } from "@/components/ui/icons";
import { posterInitials, type MovieCard as MovieCardType } from "@/lib/movie-types";
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
  // Which source failed, not that something failed: a card whose movie changes
  // under it — the featured slot advancing, a rail re-rendering in place —
  // otherwise inherits the previous movie's broken poster and shows a fallback
  // over artwork that loads perfectly well.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const posterSrc = movie.posterSrc;
  const showFallback = !posterSrc || failedSrc === posterSrc;
  const detailHref = href ?? `/ui-preview/movies/${movie.id}`;

  return (
    <article className={`poster-card poster-card-${density}`}>
      {/*
        One link per card, wrapping the artwork and the caption. They used to be
        two anchors to the same href, which cost a keyboard viewer an extra stop
        on every card in a rail — 45 of them for a rail of nine — and announced
        the same destination twice. The caption stays inside `.poster-card-copy`
        because the featured slot hides that block to print its own title.
      */}
      <Link aria-label={`Open ${movie.title}`} className="poster-card-link" href={detailHref}>
        <span className="poster-frame">
          {showFallback || !posterSrc ? (
            <PosterFallbackMark title={movie.title} />
          ) : (
            <Image
              alt={movie.posterAlt}
              fill
              onError={() => setFailedSrc(posterSrc)}
              priority={priority}
              sizes="(max-width: 480px) 44vw, (max-width: 900px) 28vw, 220px"
              src={posterSrc}
            />
          )}
          {movie.rank ? <span className="rank-badge">#{movie.rank}</span> : null}
          <span className="poster-open-cue">
            Open <Icon name="arrow" />
          </span>
        </span>
        <div className="poster-card-copy">
          <div>
            <span className="poster-title">{movie.title}</span>
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
      </Link>
      {density === "standard" && movie.reason ? (
        <p className="poster-reason">{movie.reason}</p>
      ) : null}
    </article>
  );
}

/**
 * The one fallback mark in the product. Exported because detail, the Library
 * rows, and the Quick Picks deck all need the identical treatment: a missing
 * poster must not look like a different kind of gap one route later. The rule
 * that derives the mark lives in `lib/movie-types.ts`; pass it a display title.
 */
export function PosterFallbackMark({ title }: { title: string }) {
  return (
    <span className="poster-fallback" data-testid="poster-fallback">
      <span aria-hidden="true">{posterInitials(title)}</span>
      <span>Artwork unavailable</span>
    </span>
  );
}
