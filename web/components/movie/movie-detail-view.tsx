"use client";

/**
 * Movie detail rendered from the catalog contract.
 *
 * The route is a decision surface, so the order is the movie, then what it is,
 * then the decision; provenance and technical evidence sit behind one
 * deliberate disclosure rather than in front of the movie.
 *
 * Two truthfulness rules shape what is here. Metadata gaps are named for what
 * they are — a partial or unavailable record says so and falls back to data we
 * hold — and the explanation block renders only when a structured explanation
 * is actually supplied. Today the detail resource carries none, so nothing is
 * shown; the alternative would be inventing a reason or dressing a rank score
 * up as a match percentage, and neither is a claim this system can support.
 *
 * The enriched TMDB record — tagline, runtime, backdrop, aggregate score,
 * credits, trailer — arrives on the detail response only, and every part of it
 * is optional. So the layout is written the other way round from the usual: the
 * degraded page is the base case (poster left, identity right, exactly what
 * this route rendered before), and each field that is present upgrades one
 * region. A record with no `details` at all renders the page it always did,
 * with no empty frames where a backdrop or a cast row would have gone.
 */

import { useId, useState } from "react";
import Image from "next/image";
import Link from "next/link";

import { MovieCredits } from "@/components/movie/movie-credits";
import { MovieStatePanel } from "@/components/movie/movie-state-panel";
import { MovieTrailerSection } from "@/components/movie/movie-trailer";
import { PosterFallbackMark } from "@/components/movie/poster-card";
import { Drawer } from "@/components/ui/drawer";
import { Icon } from "@/components/ui/icons";
import type {
  CatalogItem,
  MovieDetailItem,
  MovieDetails,
  MovieState,
} from "@/lib/api";
import {
  genresText,
  metadataSummary,
  overviewText,
  releaseYearText,
} from "@/lib/browse/catalog-card";
import { runtimeText, tmdbScoreText } from "@/lib/movie-details";
import type { MovieStateClient } from "@/lib/movie-state/client";
import { displayTitle } from "@/lib/movie-types";
import { returnHrefLabel } from "@/lib/navigation";
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
  stateClient,
  onCommitted,
}: {
  item: MovieDetailItem;
  userId: number;
  backHref: string;
  requestId: string;
  explanation?: MovieExplanation | null;
  stateClient?: MovieStateClient;
  onCommitted?: (state: MovieState) => void;
}) {
  // One name for this movie on this page: the heading, the poster mark, and
  // every sentence the state controls announce. The metadata line below already
  // prints the release year, so the title must not print it again.
  const title = displayTitle(item.title, item.release_year);
  const details = item.details;
  const sectionIds = useId();
  const score = tmdbScoreText(details?.tmdb_rating);

  return (
    <article className="movie-detail" aria-labelledby="movie-title">
      <div className="movie-detail-hero">
        {details?.backdrop_url ? <Backdrop src={details.backdrop_url} /> : null}

        <div className="movie-detail-poster">
          <DetailPoster posterUrl={item.poster_url} title={title} />
        </div>

        <div className="movie-detail-copy">
          <p className="eyebrow">{metadataSummary(item)}</p>
          <h1 className="display-title" id="movie-title">
            {title}
          </h1>
          {details?.tagline ? (
            <p className="movie-detail-tagline">{details.tagline}</p>
          ) : null}
          <p className="movie-detail-meta">{metaLine(item, details)}</p>

          {score ? (
            <p className="movie-detail-score">
              <Icon
                aria-hidden="true"
                className="movie-detail-score-star"
                name="star"
              />
              <span>{score}</span>
            </p>
          ) : null}

          <p className="movie-overview">{overviewText(item)}</p>

          {explanation ? (
            <section aria-label="Why this movie" className="movie-explanation">
              <p className="movie-explanation-reason">{explanation.reason}</p>
              <p className="movie-explanation-meta">
                {explanation.servingPolicy} · {explanation.modelVersion}
              </p>
            </section>
          ) : null}

          <MovieStatePanel
            client={stateClient}
            initialState={item.state}
            movieId={item.movie_id}
            onCommitted={onCommitted}
            title={title}
            userId={userId}
          />
        </div>
      </div>

      <div className="movie-detail-extras">
        {details?.trailer ? (
          <MovieTrailerSection
            headingId={`${sectionIds}-trailer`}
            stillUrl={details.backdrop_url ?? item.poster_url}
            title={title}
            trailer={details.trailer}
          />
        ) : null}

        {details ? (
          <MovieCredits
            cast={details.cast}
            directors={details.directors}
            headingId={`${sectionIds}-credits`}
          />
        ) : null}

        {details ? <TmdbAttribution /> : null}

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
                <dd>
                  {item.tmdb_id
                    ? `TMDB ${item.tmdb_id}`
                    : "MovieLens metadata only"}
                </dd>
              </div>
              <div>
                <dt>Enriched details</dt>
                <dd>
                  {details
                    ? `Snapshot taken ${details.fetched_at}`
                    : "Not part of this record"}
                </dd>
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
              Detail metadata is read from the local catalog snapshot. This
              route makes no live third-party request, and it carries no ranking
              score — a movie can be in the catalog without being eligible for
              any serving policy.
            </p>
          </Drawer>

          {/*
            Named after where it actually goes. A movie opened from Library or
            from Discover returns to the collection it was opened from, and a
            link that said "Browse" regardless was the last place the route
            still assumed one entry point.
          */}
          <Link className="button-quiet" href={backHref}>
            <Icon name="chevron-left" />
            {returnHrefLabel(backHref)}
          </Link>
        </div>
      </div>
    </article>
  );
}

/**
 * Year · runtime · genres.
 *
 * Runtime joins the line it belongs on rather than getting a row of its own:
 * it is the same kind of fact as the year, and a two-word statistic does not
 * earn a heading. A record without one simply reads as it always did.
 */
function metaLine(item: CatalogItem, details: MovieDetails | null): string {
  const runtime = runtimeText(details?.runtime_minutes);
  return [releaseYearText(item), runtime, genresText(item)]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The backdrop, used as a wash rather than as an image.
 *
 * It sits behind the poster and the title at low opacity under a gradient that
 * resolves to the page's own ground, so the type never competes with a still
 * frame for contrast — the reason the hero is readable at all is that the
 * bottom of the gradient *is* `--surface-canvas`. It is decorative: the poster
 * beside it carries the movie's identity, and a reader loses nothing by not
 * hearing this described.
 */
function Backdrop({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;

  return (
    <div aria-hidden="true" className="movie-detail-backdrop">
      {/*
        Deliberately not `priority`. The poster is this page's LCP element and
        already holds that slot; a 1280px decorative wash competing for the same
        preload would trade a measured budget for atmosphere. It occupies a
        fixed box either way, so arriving late costs no layout shift.
      */}
      <Image
        alt=""
        fetchPriority="low"
        // Explicit dimensions rather than `fill`: the box is fixed either way,
        // but the width/height attributes are what declares the intrinsic ratio
        // up front, which is the property the reserved-box CLS guard reads.
        // The stylesheet stretches and crops it to the veil.
        height={720}
        onError={() => setFailed(true)}
        sizes="100vw"
        src={src}
        width={1280}
      />
      <span className="movie-detail-backdrop-veil" />
    </div>
  );
}

/**
 * Required by TMDB's terms wherever their data is shown, and honest about what
 * it covers: the enriched fields on this page, not the whole product. The
 * shell-level attribution the product still owes is a separate piece of work.
 */
function TmdbAttribution() {
  return (
    <p className="movie-detail-attribution">
      <a href="https://www.themoviedb.org" rel="noreferrer" target="_blank">
        <Image alt="TMDB" height={13} src="/tmdb-logo.svg" width={100} />
      </a>
      <span>
        Details from TMDB. This product uses the TMDB API but is not endorsed or
        certified by TMDB.
      </span>
    </p>
  );
}

function DetailPoster({
  posterUrl,
  title,
}: {
  posterUrl: string | null;
  title: string;
}) {
  // The failing source, not a bare boolean: this route remounts per movie
  // today, but a client-side transition between two detail pages would
  // otherwise carry one movie's broken poster onto the next one's artwork.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const showImage = Boolean(posterUrl) && failedSrc !== posterUrl;

  return (
    <div className="poster-frame detail-poster">
      {showImage && posterUrl ? (
        <Image
          alt=""
          fill
          onError={() => setFailedSrc(posterUrl)}
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
