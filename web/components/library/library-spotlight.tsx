"use client";

/**
 * One seen title at a time, above the list it is walking.
 *
 * The Seen tab answers two different questions and they want different shapes.
 * "What have I watched?" is a list — dense, filterable, sortable. "What was
 * that one like?" is a single movie given room, which is what Discover's
 * featured slot already is for an unseen title. So the spotlight is that
 * presentation pointed at the collection the reader is already looking at.
 *
 * Three rules keep it honest rather than decorative:
 *
 * - **It walks the rows.** Its queue is the loaded window, in the order the
 *   rows are in, under the same filters and sort. There is no second fetch and
 *   no second ordering, so a position is an index into what is on screen.
 * - **The base layer never waits.** Poster, title, year, genres, the seen-on
 *   date, the rating control and the actions all come from the row the list
 *   already has. The enriched fields — backdrop, runtime, crowd score, cast —
 *   are read from the detail resource for the current title only, and they are
 *   *added* when they arrive. A read that fails, times out, or 404s is silent:
 *   this is progressive enhancement of a card that is already complete, and an
 *   error region here would report a problem the reader does not have.
 * - **It writes through the one write path.** Every action is reported to the
 *   route, which owns the mutation exactly as it does for a row. The spotlight
 *   adds no transport, and it declares its controls with the same
 *   `libraryControlSet` call the row makes so the two cannot drift apart.
 */

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  libraryControlSet,
  libraryRemovalConfirmation,
} from "@/components/library/library-row";
import { PosterCard } from "@/components/movie/poster-card";
import { MovieStateControls } from "@/components/movie/movie-state-controls";
import { RatingStars } from "@/components/movie/rating-stars";
import { Icon } from "@/components/ui/icons";
import type { LibraryMovie, MovieDetailResponse, MovieDetails } from "@/lib/api";
import { seenOnText } from "@/lib/library/collection";
import { displayState, ratingAction, type MovieStateAction } from "@/lib/movie-state/actions";
import { runtimeText, tmdbScoreText } from "@/lib/movie-details";
import { displayTitle, type MovieCard } from "@/lib/movie-types";
import { hasResourceData, type ResourceState } from "@/lib/resources/state";
import "./library-spotlight.css";

/** Focus lands here after a removal, so the section has to be able to take it. */
export const LIBRARY_SPOTLIGHT_ID = "library-spotlight";

/** The one sentence about what a star does and does not commit to. */
export const SPOTLIGHT_RATING_NOTE =
  "The star value is display feedback today, not a graded training signal.";

export type SpotlightDetailReader = (
  movieId: number,
  signal: AbortSignal,
) => Promise<ResourceState<MovieDetailResponse>>;

export function LibrarySpotlight({
  movie,
  persona,
  href,
  position,
  total,
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
  onAction,
  readDetail,
  busy,
}: {
  /** The row the spotlight is on. Everything below the enrichment comes from it. */
  movie: LibraryMovie;
  persona: string;
  href: string;
  /** One-based, for the readout. */
  position: number;
  /** `page.matched` — every row this query has, not just the loaded window. */
  total: number;
  hasPrevious: boolean;
  hasNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onAction: (action: MovieStateAction, control: HTMLElement) => void;
  readDetail: SpotlightDetailReader;
  busy: boolean;
}) {
  const movieId = movie.movie_id;
  const year = movie.release_year ?? null;
  const title = displayTitle(movie.title, year);

  const [details, setDetails] = useState<Record<number, MovieDetails | null>>({});
  // Which titles have already been asked for, in a ref so the effect depends on
  // the movie rather than on its own results.
  const requested = useRef(new Set<number>());
  const [announcement, setAnnouncement] = useState("");
  // Set by the two move handlers, so the live region speaks for a navigation
  // and stays quiet when the window moved underneath the reader instead.
  const navigated = useRef(false);

  useEffect(() => {
    if (requested.current.has(movieId)) return;
    requested.current.add(movieId);

    // One read in flight: a reader pressing `Next` five times must not leave
    // five detail requests racing, and the answer to a title they have already
    // moved past is worth nothing.
    const controller = new AbortController();
    let current = true;
    void readDetail(movieId, controller.signal).then((state) => {
      if (!current) return;
      setDetails((held) => ({
        ...held,
        // A failure is recorded as "no enrichment" rather than surfaced. The
        // `ResourceFailure` still carries its request ID for the logs.
        [movieId]: hasResourceData(state) ? (state.data.item.details ?? null) : null,
      }));
    });
    return () => {
      current = false;
      controller.abort();
    };
  }, [movieId, readDetail]);

  const detail = details[movieId] ?? null;
  const score = tmdbScoreText(detail?.tmdb_rating);
  const runtime = runtimeText(detail?.runtime_minutes);
  const cast = detail?.cast.slice(0, 3).map((member) => member.name) ?? [];

  // Arms the live region, then hands the move to the route: the spotlight's
  // position is the route's state, because the window it walks is.
  const move = useCallback((step: () => void) => {
    navigated.current = true;
    step();
  }, []);

  // The readout the reader hears, said only after they moved the spotlight.
  useEffect(() => {
    if (!navigated.current) return;
    navigated.current = false;
    setAnnouncement(
      `${title}, ${year ?? "year unknown"}. ${position} of ${total} in Seen.`,
    );
  }, [movieId, position, title, total, year]);

  const card: MovieCard = {
    id: movieId,
    title,
    year,
    genres: movie.genres,
    posterSrc: movie.poster_url ?? null,
    // Decorative: the title sits beside it inside a link that already names the
    // movie, which is the rule every other poster on the product follows.
    posterAlt: "",
    overview: null,
    state: displayState(movie.state),
  };

  const metaLine = [year ?? "Year unknown", runtime, movie.genres.join(" · ") || "Genres unavailable"]
    .filter(Boolean)
    .join(" · ");

  return (
    <section
      aria-label="Seen spotlight"
      className="library-spotlight"
      id={LIBRARY_SPOTLIGHT_ID}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        // The star row owns the arrow keys for its roving tab stop, and the
        // filter fields own them for text editing. Stealing either would break
        // a binding the reader already knows.
        const target = event.target as HTMLElement | null;
        if (target?.closest("input, select, textarea, .rating-stars")) return;
        event.preventDefault();
        if (event.key === "ArrowLeft" && hasPrevious) move(onPrevious);
        if (event.key === "ArrowRight" && hasNext) move(onNext);
      }}
      tabIndex={-1}
    >
      {detail?.backdrop_url ? <SpotlightBackdrop src={detail.backdrop_url} /> : null}

      <div className="library-spotlight-poster">
        <PosterCard href={href} movie={card} />
      </div>

      <div className="library-spotlight-copy">
        <div className="library-spotlight-nav">
          <button
            aria-label="Previous seen title"
            className="button-secondary library-spotlight-step"
            disabled={!hasPrevious}
            onClick={() => move(onPrevious)}
            type="button"
          >
            <Icon name="chevron-left" />
            Previous
          </button>
          <p className="library-spotlight-position">
            {position} of {total}
          </p>
          <button
            aria-label="Next seen title"
            className="button-secondary library-spotlight-step"
            disabled={!hasNext}
            onClick={() => move(onNext)}
            type="button"
          >
            Next
            <Icon name="arrow" />
          </button>
        </div>

        <h3 className="display-title library-spotlight-title">{title}</h3>
        <p className="library-spotlight-meta">{metaLine}</p>
        {score ? (
          <p className="library-spotlight-score">
            <Icon aria-hidden="true" name="star" />
            <span>{score}</span>
          </p>
        ) : null}
        <p className="library-spotlight-seen">{seenOnText(movie.state.watched_at)}</p>
        {cast.length ? (
          <p className="library-spotlight-cast">With {cast.join(", ")}</p>
        ) : null}

        <RatingStars
          busy={busy}
          className="library-spotlight-rating"
          clearLabel="Clear rating"
          idPrefix={`library-spotlight-${movieId}`}
          // Remounted per title: the control's collapse-and-chip sequence is
          // about one movie's rating, and carrying its phase across an advance
          // would celebrate the previous title's star over the new one.
          key={movieId}
          legend="Your rating"
          note={SPOTLIGHT_RATING_NOTE}
          onRate={(value, control) => onAction(ratingAction(value), control)}
          rating={movie.state.rating}
          title={title}
        />

        <MovieStateControls
          busy={busy}
          classNames={{
            root: "library-spotlight-actions",
            action: "library-action",
            confirm: "library-spotlight-confirm",
          }}
          confirmation={libraryRemovalConfirmation(title, persona)}
          controls={libraryControlSet("history", movie.state.watched_at !== null)}
          idPrefix={`library-spotlight-${movieId}`}
          key={`controls-${movieId}`}
          onAction={onAction}
          state={displayState(movie.state)}
          title={title}
        />
      </div>

      {/*
        Navigation only. Mutations keep announcing through the route's own
        region: two live regions in one panel is how one of them stops being
        read, so they are split by subject rather than duplicated.
      */}
      <p aria-live="polite" className="visually-hidden">
        {announcement}
      </p>
    </section>
  );
}

/**
 * The backdrop as a wash rather than as an image, the same treatment movie
 * detail uses: it ends on the page's own ground, so nothing on top of it is
 * competing with a still frame for contrast. Decorative — the poster beside it
 * carries the movie's identity.
 */
function SpotlightBackdrop({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;

  return (
    <div aria-hidden="true" className="library-spotlight-backdrop">
      {/*
        Explicit dimensions rather than `fill`: the box is fixed either way, but
        the width/height attributes are what declare the intrinsic ratio up
        front, which is the property the reserved-box CLS guard reads.
      */}
      <Image
        alt=""
        fetchPriority="low"
        height={720}
        onError={() => setFailed(true)}
        sizes="100vw"
        src={src}
        width={1280}
      />
      <span className="library-spotlight-veil" />
    </div>
  );
}
