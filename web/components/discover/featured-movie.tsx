import Link from "next/link";

import { PosterCard } from "@/components/movie/poster-card";
import {
  MovieStateControls,
  PREVIEW_CONTROLS,
} from "@/components/movie/movie-state-controls";
import { Drawer } from "@/components/ui/drawer";
import { Icon } from "@/components/ui/icons";
import type { DecisionDirection } from "@/lib/movie-state/actions";
import type { EvidenceRecord, MovieCard } from "@/lib/movie-types";
import "./featured-movie.css";

/**
 * The primary movie: the first thing a viewer should read on Discover.
 *
 * The slots exist so the live route can supply its own eyebrow, movie URL,
 * mutating controls, and evidence disclosure without a second copy of this
 * layout. Left alone, it renders the recorded preview exactly as Bundle 4 did.
 */
export function FeaturedMovie({
  movie,
  evidence,
  eyebrow,
  href,
  actions,
  aside,
  disclosure,
  enterFrom = null,
}: {
  movie: MovieCard;
  evidence?: EvidenceRecord;
  eyebrow?: React.ReactNode;
  href?: string;
  actions?: React.ReactNode;
  /** Rendered under the actions: rating, mutation status, honesty notes. */
  aside?: React.ReactNode;
  disclosure?: React.ReactNode;
  /**
   * Which way the decision that produced this card travelled, so the arrival
   * reads as the consequence of the press rather than as a page change. The
   * caller passes `null` under reduced motion; the stylesheet holds the same
   * rule, so an animation can never sneak back in through a re-render.
   */
  enterFrom?: DecisionDirection | null;
}) {
  const movieHref = href ?? `/ui-preview/movies/${movie.id}`;
  return (
    <section
      aria-labelledby="featured-title"
      className="featured-movie"
      data-enter-from={enterFrom ?? undefined}
    >
      <div className="featured-poster">
        <PosterCard href={movieHref} movie={movie} priority />
      </div>
      <div className="featured-copy">
        <div className="featured-identity">
          <p className="eyebrow">
            {eyebrow ?? <>Tonight&apos;s first look · Rank {movie.rank ?? 1}</>}
          </p>
          <h1 className="display-title" id="featured-title">
            {movie.title}
          </h1>
          <p className="featured-meta">
            {movie.year ?? "Year unknown"} <span aria-hidden="true">/</span> {movie.genres.join(" · ") || "Genre unavailable"}
          </p>
        </div>
        <p className="featured-reason">{movie.reason ?? "Selected from the current ranked recommendation set."}</p>
        <div className="featured-actions">
          <Link className="button-primary" href={movieHref}>
            Open movie <Icon name="arrow" />
          </Link>
          {actions ?? (
            <MovieStateControls
              controls={PREVIEW_CONTROLS}
              initialState={movie.state}
              title={movie.title}
            />
          )}
        </div>
        {aside}
        {disclosure ??
          (evidence ? (
            <Drawer buttonLabel="Why this?" eyebrow="Model evidence" title={`Why ${movie.title}?`}>
              <EvidenceDetails evidence={evidence} reason={movie.reason} />
            </Drawer>
          ) : null)}
      </div>
    </section>
  );
}

export function EvidenceDetails({ evidence, reason }: { evidence: EvidenceRecord; reason?: string }) {
  return (
    <div className="evidence-details">
      <p className="evidence-reason">{reason ?? "No item-level reason was recorded."}</p>
      <dl>
        <div><dt>Serving policy</dt><dd>{evidence.policy}</dd></div>
        <div><dt>Ranker</dt><dd>{evidence.modelVersion}</dd></div>
        <div><dt>Candidates</dt><dd>{evidence.candidateVersion}</dd></div>
        <div><dt>Features</dt><dd>{evidence.featureVersion}</dd></div>
        <div><dt>Request</dt><dd>{evidence.requestId}</dd></div>
        <div><dt>API latency</dt><dd>{evidence.latencyMs} ms</dd></div>
      </dl>
      <p className="fixture-note">Recorded contract fixture. This surface does not claim calibrated match probability.</p>
    </div>
  );
}
