"use client";

/**
 * Movie detail rendered from the catalog contract.
 *
 * The route is a decision surface, so the order is poster, identity, synopsis,
 * then the state controls; provenance and technical evidence sit behind one
 * deliberate disclosure rather than in front of the movie.
 *
 * Two truthfulness rules shape what is here. Metadata gaps are named for what
 * they are — a partial or unavailable record says so and falls back to data we
 * hold — and the explanation block renders only when a structured explanation
 * is actually supplied. Today the detail resource carries none, so nothing is
 * shown; the alternative would be inventing a reason or dressing a rank score
 * up as a match percentage, and neither is a claim this system can support.
 */

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";

import { CanonicalStateControls } from "@/components/movie/canonical-state-controls";
import { PosterFallbackMark } from "@/components/movie/poster-card";
import { Drawer } from "@/components/ui/drawer";
import { Icon } from "@/components/ui/icons";
import type { CatalogItem, MovieState } from "@/lib/api";
import {
  genresText,
  metadataSummary,
  overviewText,
  releaseYearText,
} from "@/lib/browse/catalog-card";
import "@/components/movie/poster-card.css";
import "./movie-detail-view.css";

/**
 * The shape a structured explanation would have to arrive in.
 *
 * The field names track the recommendation contract's `reason`,
 * `serving_policy`, and `model_version` so an explanation can be handed
 * straight from a recommendation response without a translation layer that
 * could quietly rename a claim. The detail resource carries none of this
 * today, so nothing renders; there is no default, because a default here
 * would be a fabricated reason.
 */
export type MovieExplanation = {
  reason: string;
  servingPolicy: string;
  modelVersion: string;
};

export function MovieDetailView({
  item,
  userId,
  backHref,
  requestId,
  explanation = null,
  onCommitted,
}: {
  item: CatalogItem;
  userId: number;
  backHref: string;
  requestId: string;
  explanation?: MovieExplanation | null;
  onCommitted?: (state: MovieState) => void;
}) {
  return (
    <article className="movie-detail" aria-labelledby="movie-title">
      <div className="movie-detail-poster">
        <DetailPoster posterUrl={item.poster_url} title={item.title} />
      </div>

      <div className="movie-detail-copy">
        <p className="eyebrow">{metadataSummary(item)}</p>
        <h1 className="display-title" id="movie-title">
          {item.title}
        </h1>
        <p className="movie-detail-meta">
          {releaseYearText(item)} · {genresText(item)}
        </p>
        <p className="movie-overview">{overviewText(item)}</p>

        {explanation ? (
          <section aria-label="Why this movie" className="movie-explanation">
            <p className="movie-explanation-reason">{explanation.reason}</p>
            <p className="movie-explanation-meta">
              {explanation.servingPolicy} · {explanation.modelVersion}
            </p>
          </section>
        ) : null}

        <CanonicalStateControls
          initialState={item.state}
          movieId={item.movie_id}
          onCommitted={onCommitted}
          title={item.title}
          userId={userId}
        />

        <div className="movie-detail-disclosure">
          <Drawer
            buttonLabel="Record details"
            eyebrow="Technical evidence"
            title="How this record was assembled"
          >
            <dl className="record-details">
              <div>
                <dt>Metadata source</dt>
                <dd>{item.metadata_source}</dd>
              </div>
              <div>
                <dt>Record completeness</dt>
                <dd>{item.source_status}</dd>
              </div>
              <div>
                <dt>External reference</dt>
                <dd>{item.tmdb_id ? `TMDB ${item.tmdb_id}` : "MovieLens metadata only"}</dd>
              </div>
              <div>
                <dt>Recorded interactions</dt>
                <dd>{item.interaction_count} in this tenant</dd>
              </div>
              <div>
                <dt>Request</dt>
                <dd>{requestId}</dd>
              </div>
            </dl>
            <p className="record-note">
              Detail metadata is read from the local catalog snapshot. This route
              makes no live third-party request, and it carries no ranking score
              — a movie can be in the catalog without being eligible for any
              serving policy.
            </p>
          </Drawer>

          <Link className="button-quiet" href={backHref}>
            <Icon name="chevron-left" />
            Back to Browse
          </Link>
        </div>
      </div>
    </article>
  );
}

function DetailPoster({
  posterUrl,
  title,
}: {
  posterUrl: string | null;
  title: string;
}) {
  const [failed, setFailed] = useState(false);
  const showImage = Boolean(posterUrl) && !failed;

  return (
    <div className="poster-frame detail-poster">
      {showImage && posterUrl ? (
        <Image
          alt=""
          fill
          onError={() => setFailed(true)}
          priority
          sizes="(max-width: 768px) 80vw, 340px"
          src={posterUrl}
        />
      ) : (
        <PosterFallbackMark title={title} />
      )}
    </div>
  );
}
