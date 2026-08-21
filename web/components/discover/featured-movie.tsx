import Link from "next/link";

import { PosterCard } from "@/components/movie/poster-card";
import { StateControls } from "@/components/movie/state-controls";
import { Drawer } from "@/components/ui/drawer";
import { Icon } from "@/components/ui/icons";
import type { EvidenceRecord, MovieCard } from "@/lib/movie-types";
import "./featured-movie.css";

export function FeaturedMovie({ movie, evidence }: { movie: MovieCard; evidence?: EvidenceRecord }) {
  return (
    <section className="featured-movie" aria-labelledby="featured-title">
      <div className="featured-poster">
        <PosterCard movie={movie} priority />
      </div>
      <div className="featured-copy">
        <div className="featured-identity">
          <p className="eyebrow">Tonight&apos;s first look · Rank {movie.rank ?? 1}</p>
          <h1 className="display-title" id="featured-title">
            {movie.title}
          </h1>
          <p className="featured-meta">
            {movie.year ?? "Year unknown"} <span aria-hidden="true">/</span> {movie.genres.join(" · ") || "Genre unavailable"}
          </p>
        </div>
        <p className="featured-reason">{movie.reason ?? "Selected from the current ranked recommendation set."}</p>
        <div className="featured-actions">
          <Link className="button-primary" href={`/ui-preview/movies/${movie.id}`}>
            Open movie <Icon name="arrow" />
          </Link>
          <StateControls initialState={movie.state} title={movie.title} />
        </div>
        {evidence ? (
          <Drawer buttonLabel="Why this?" eyebrow="Model evidence" title={`Why ${movie.title}?`}>
            <EvidenceDetails evidence={evidence} reason={movie.reason} />
          </Drawer>
        ) : null}
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
