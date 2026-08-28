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
          <span className="poster-open-cue">
            Open <Icon name="arrow" />
          </span>
        </span>
        {/*
          The rank hangs beside the caption rather than sitting on the artwork.
          A badge pinned to the poster's top-left covered whatever the designer
          of that poster put there, and on a pale sheet — Forrest Gump, Toy
          Story — the cream disc all but disappeared into it. In the gutter it
          costs no vertical space, never obscures a frame, and reads as what it
          is: a position in an edited list. It stays inside the link because the
          link's `aria-label` already names the movie, so a screen reader is not
          handed a bare numeral before every title.
        */}
        <div className="poster-card-copy">
          {movie.rank ? (
            <span aria-hidden="true" className="poster-rank">
              {movie.rank}
            </span>
          ) : null}
          {/* Clamped to two reserved lines; the full title is on the link's
              accessible name, and `title` gives a pointer viewer the rest. */}
          <span className="poster-title" title={movie.title}>
            {movie.title}
          </span>
          {movie.state.watchlisted ? (
            <span aria-label="In watchlist" className="poster-state" role="img">
              <Icon name="bookmark" />
            </span>
          ) : null}
          <p className="poster-meta">
            {movie.year ?? "Year unknown"}
            {movie.genres[0] ? ` · ${movie.genres[0]}` : " · Genre unavailable"}
          </p>
          {metadataNote ? <p className="poster-metadata-note">{metadataNote}</p> : null}
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
