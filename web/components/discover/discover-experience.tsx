"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { FeaturedMovie } from "@/components/discover/featured-movie";
import { WhyThis } from "@/components/discover/why-this";
import type { PreloadedTechnicalEvidence } from "@/components/discover/technical-evidence";
import { MovieRail } from "@/components/movie/movie-rail";
import { RatingControl } from "@/components/movie/rating-control";
import {
  StateControls,
  type MovieStateAction,
} from "@/components/movie/state-controls";
import { ResourceProblem, ResourceRegion } from "@/components/ui/resource-region";
import { EmptyState, PosterSkeleton } from "@/components/ui/resource-states";
import type { MovieState, RecommendationResponse } from "@/lib/api";
import {
  movieCardState,
  optimisticCardState,
  recommendationCards,
} from "@/lib/discover/movie-card";
import { describeServingPolicy } from "@/lib/discover/policy";
import type { MovieCard, MovieState as MovieCardState } from "@/lib/movie-types";
import { discoverMutationAnnouncement } from "@/lib/discover/announce";
import {
  readCommittedStates,
  recordCommittedState,
} from "@/lib/movie-state/committed-store";
import {
  mutateMovieState,
  type MovieStateMutationResult,
  type MovieStateResource,
} from "@/lib/movie-state/mutate";
import { MOVIE_DETAIL, RECOMMENDATIONS } from "@/lib/resources/definitions";
import { readBffResource } from "@/lib/resources/browser";
import {
  hasResourceData,
  isResourceFailure,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";
import "./discover.css";

const STATUS_ANCHOR = "discover-status";

type Flow =
  | { kind: "idle" }
  | { kind: "saving"; message: string }
  | { kind: "refreshing"; message: string }
  | { kind: "refreshed"; message: string }
  | { kind: "refresh-failed"; message: string; failure: ResourceFailure }
  | { kind: "error"; message: string };

/**
 * The interactive half of `/discover`.
 *
 * It is seeded with the server-loaded recommendation state rather than
 * fetching on mount, so the first movie is in the initial HTML. After that it
 * owns three things the server cannot: the optimistic frame of a mutation, the
 * canonical revision each committed response returns, and the re-read that
 * makes "Recommendations refreshed" true rather than decorative. That claim is
 * only ever made after the refetch resolves — a failed refetch says so.
 */
export function DiscoverExperience({
  userId,
  personaName,
  initialRecommendations,
  recordedEvidence,
  browseHref,
  movieHrefBase,
  movieHrefQuery = "",
  limit,
}: {
  userId: number;
  personaName: string;
  initialRecommendations: ResourceState<RecommendationResponse>;
  recordedEvidence?: PreloadedTechnicalEvidence | null;
  browseHref: string;
  /**
   * Built from strings rather than a callback: this component is a client
   * boundary, and a server component cannot hand a function across it.
   */
  movieHrefBase: string;
  movieHrefQuery?: string;
  limit: number;
}) {
  const movieHrefFor = (movieId: number) =>
    `${movieHrefBase}/${movieId}${movieHrefQuery}`;
  const router = useRouter();
  const [recommendations, setRecommendations] = useState(initialRecommendations);
  const [displayStates, setDisplayStates] = useState<Record<number, MovieCardState>>({});
  const [revisions, setRevisions] = useState<Record<number, number>>({});
  const [pendingMovieId, setPendingMovieId] = useState<number | null>(null);
  const [flow, setFlow] = useState<Flow>({ kind: "idle" });
  const [justWatched, setJustWatched] = useState<MovieCard | null>(null);

  const refetch = useCallback(
    () =>
      readBffResource(
        RECOMMENDATIONS,
        `/api/users/${userId}/recommendations?limit=${limit}`,
      ),
    [limit, userId],
  );

  const reload = useCallback(async () => {
    setRecommendations({ status: "loading", resource: "recommendations" });
    setRecommendations(await refetch());
  }, [refetch]);

  function restoreFocus(id: string) {
    requestAnimationFrame(() => document.getElementById(id)?.focus());
  }

  /**
   * Records a canonical state the API committed, in the three places that have
   * to agree: what the card shows, the revision the next write asserts, and the
   * tab-local relay the other routes read.
   */
  const adoptCommittedState = useCallback(
    (state: MovieState) => {
      setDisplayStates((current) => ({
        ...current,
        [state.movie_id]: movieCardState(state),
      }));
      setRevisions((current) => ({ ...current, [state.movie_id]: state.revision }));
      if (typeof window !== "undefined") {
        recordCommittedState(window.sessionStorage, userId, state);
      }
    },
    [userId],
  );

  /**
   * A recommendation response carries no per-item state, so a first write can
   * only assert revision 0 and a title already saved elsewhere answers `409`.
   * Reading that one movie's canonical record turns the conflict into a
   * correction: the control shows the truth and the next click asserts a
   * revision the server issued.
   */
  const resyncMovieState = useCallback(
    async (movieId: number) => {
      const detail = await readBffResource(
        MOVIE_DETAIL,
        `/api/users/${userId}/movies/${movieId}`,
      );
      if (!hasResourceData(detail) || !detail.data.item.state) return;
      adoptCommittedState(detail.data.item.state);
    },
    [adoptCommittedState, userId],
  );

  /**
   * The revision the next write asserts. A recommendation response carries no
   * per-item state, so this session's own commits come first, then the
   * tab-local relay of states other routes committed, and only then the
   * canonical "no state yet" value. The relay is read here rather than folded
   * into render state so nothing reads browser storage while rendering.
   */
  function knownRevision(movieId: number): number {
    if (movieId in revisions) return revisions[movieId];
    if (typeof window === "undefined") return 0;
    return readCommittedStates(window.sessionStorage, userId).get(movieId)?.revision ?? 0;
  }

  function mutationFailureCopy(
    result: Extract<MovieStateMutationResult, { status: "conflict" | "failed" }>,
    title: string,
  ): string {
    if (result.status === "conflict") {
      return `${title} changed somewhere else before this saved. Its current state has been loaded; try again.`;
    }
    if (result.failure.status === "auth-expired") {
      return `Your session expired before this saved. Sign in again to change ${title}.`;
    }
    if (result.failure.status === "forbidden") {
      return `This session is not allowed to change state for ${title}.`;
    }
    return `${title} was not saved. It was left as it was. Request ${result.failure.requestId}.`;
  }

  async function commit(
    movie: MovieCard,
    resource: MovieStateResource,
    method: "PUT" | "DELETE",
    control: HTMLButtonElement,
    rating?: number,
  ) {
    const controlId = control.id;
    const previous = displayStates;
    const currentState = displayStates[movie.id] ?? movie.state;

    setPendingMovieId(movie.id);
    setFlow({ kind: "saving", message: `Saving ${movie.title}…` });
    setDisplayStates({
      ...previous,
      [movie.id]: optimisticCardState(currentState, resource, method, rating),
    });

    const result = await mutateMovieState({
      userId,
      movieId: movie.id,
      resource,
      method,
      rating,
      // `0` is the canonical "no state yet" assertion; anything else is a
      // revision the server issued, never one invented here.
      expectedRevision: knownRevision(movie.id),
    });

    if (result.status !== "committed") {
      setDisplayStates(previous);
      setPendingMovieId(null);
      setFlow({ kind: "error", message: mutationFailureCopy(result, movie.title) });
      if (result.status === "conflict") void resyncMovieState(movie.id);
      restoreFocus(controlId);
      return;
    }

    // The committed state replaces the optimistic guess outright; a
    // reconciliation that kept any local field would be a second source of
    // truth for the same movie.
    adoptCommittedState(result.state);
    const message = discoverMutationAnnouncement(resource, method, movie.title);
    if (result.state.watched_at !== null) {
      setJustWatched({ ...movie, state: movieCardState(result.state) });
    }

    setFlow({ kind: "refreshing", message });
    const next = await refetch();
    setPendingMovieId(null);
    if (isResourceFailure(next)) {
      setFlow({ kind: "refresh-failed", message, failure: next });
    } else {
      setRecommendations(next);
      setFlow({ kind: "refreshed", message });
    }
    // Watch history is a server-rendered region on this page; the mutation
    // changed it too, so ask the server component tree to re-run.
    router.refresh();
    restoreFocus(STATUS_ANCHOR);
  }

  function onAction(movie: MovieCard) {
    return (action: MovieStateAction, control: HTMLButtonElement) => {
      void commit(movie, action.resource, action.method, control);
    };
  }

  return (
    <ResourceRegion
      empty={
        <EmptyState
          action={
            <Link className="button-primary" href={browseHref}>
              Browse the catalog
            </Link>
          }
          message="The recommendation API answered with no unseen titles for this persona. Browsing and marking a few movies gives it something to work with."
          title="No recommendations right now"
        />
      }
      label="Recommendations"
      loading={<PosterSkeleton count={5} />}
      onRetry={() => void reload()}
      state={recommendations}
    >
      {(data) => {
        const cards = recommendationCards(data.items, displayStates);
        const [featured, ...rest] = cards;
        const policy = describeServingPolicy(data);
        if (!featured) return null;
        return (
          <>
            <FeaturedMovie
              actions={
                <StateControls
                  busy={pendingMovieId === featured.id}
                  idPrefix={`featured-${featured.id}`}
                  initialState={featured.state}
                  onAction={onAction(featured)}
                  showDismiss
                  state={featured.state}
                  title={featured.title}
                />
              }
              aside={
                <FlowStatus
                  flow={flow}
                  onRetryRefresh={() => void reload()}
                  personaName={personaName}
                />
              }
              disclosure={
                <WhyThis
                  item={data.items[0]}
                  preloadedEvidence={recordedEvidence}
                  requestId={hasResourceData(recommendations) ? recommendations.requestId : null}
                  response={data}
                  userId={userId}
                />
              }
              eyebrow={
                <>
                  <span className={`policy-chip policy-chip-${policy.kind}`}>{policy.label}</span>
                  <span className="policy-rank">Rank {featured.rank ?? 1}</span>
                </>
              }
              href={movieHrefFor(featured.id)}
              movie={featured}
            />

            {justWatched ? (
              <JustWatched
                busy={pendingMovieId === justWatched.id}
                movie={justWatched}
                onRate={(value, control) =>
                  void commit(justWatched, "rating", "PUT", control, value)
                }
                state={displayStates[justWatched.id] ?? justWatched.state}
              />
            ) : null}

            {rest.length ? (
              <MovieRail
                eyebrow={policy.label}
                footer={(movie) => (
                  <StateControls
                    busy={pendingMovieId === movie.id}
                    compact
                    idPrefix={`rail-${movie.id}`}
                    initialState={movie.state}
                    onAction={onAction(movie)}
                    state={displayStates[movie.id] ?? movie.state}
                    title={movie.title}
                  />
                )}
                movieHref={(movie) => movieHrefFor(movie.id)}
                movies={rest}
                seeAllHref={browseHref}
                title="More in this ranked set"
              />
            ) : null}

            <p className="discover-browse-path">
              Nothing here?{" "}
              <Link className="button-quiet" href={browseHref}>
                Browse the whole catalog
              </Link>
            </p>
          </>
        );
      }}
    </ResourceRegion>
  );
}

function statusCopy(flow: Flow): string {
  switch (flow.kind) {
    case "saving":
      return flow.message;
    case "refreshing":
      return `${flow.message} Refreshing recommendations…`;
    case "refreshed":
      return `${flow.message} Recommendations refreshed.`;
    case "refresh-failed":
      return `${flow.message} Recommendations could not be refreshed.`;
    case "error":
      return flow.message;
    default:
      return "";
  }
}

function FlowStatus({
  flow,
  onRetryRefresh,
  personaName,
}: {
  flow: Flow;
  onRetryRefresh: () => void;
  personaName: string;
}) {
  return (
    <div className="discover-status">
      <p
        aria-live="polite"
        className={`discover-status-copy discover-status-${flow.kind}`}
        id={STATUS_ANCHOR}
        role="status"
        tabIndex={-1}
      >
        {statusCopy(flow) ||
          `Recorded feedback updates ${personaName}'s live history and the next recommendation request.`}
      </p>
      {flow.kind === "refresh-failed" ? (
        <ResourceProblem
          failure={flow.failure}
          label="Recommendations"
          onRetry={onRetryRefresh}
        />
      ) : null}
    </div>
  );
}

/**
 * A watched title leaves the recommendation set on the next request, so the
 * rating control cannot live on the card that triggered it. Anchoring it here
 * keeps `Watched → Rate` reachable and keeps the honest caveat next to it.
 */
function JustWatched({
  movie,
  state,
  busy,
  onRate,
}: {
  movie: MovieCard;
  state: MovieCardState;
  busy: boolean;
  onRate: (value: number, control: HTMLButtonElement) => void;
}) {
  return (
    <section aria-labelledby="just-watched-heading" className="just-watched">
      <div>
        <p className="eyebrow">Just marked watched</p>
        <h2 className="section-title" id="just-watched-heading">
          Rate {movie.title}
        </h2>
      </div>
      <RatingControl
        busy={busy}
        idPrefix={`just-watched-${movie.id}`}
        note="Stars are recorded feedback for this persona. The deployed recommender counts any rating as one observed watch, so a 1 and a 5 are the same learned signal today."
        onRate={onRate}
        rating={state.rating}
        title={movie.title}
      />
    </section>
  );
}
