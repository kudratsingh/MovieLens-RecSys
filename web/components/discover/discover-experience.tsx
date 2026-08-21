"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { FeaturedMovie } from "@/components/discover/featured-movie";
import { QuickPicksEntry } from "@/components/discover/quick-picks-entry";
import { WhyThis } from "@/components/discover/why-this";
import type { PreloadedTechnicalEvidence } from "@/components/discover/technical-evidence";
import { MovieRail } from "@/components/movie/movie-rail";
import {
  MovieRatingControl,
  MovieStateControls,
  RECOMMENDATION_CONTROLS,
} from "@/components/movie/movie-state-controls";
import { ResourceProblem, ResourceRegion } from "@/components/ui/resource-region";
import { EmptyState, PosterSkeleton } from "@/components/ui/resource-states";
import type { MovieState, RecommendationResponse } from "@/lib/api";
import { recommendationCards } from "@/lib/discover/movie-card";
import { describeServingPolicy } from "@/lib/discover/policy";
import type { MovieCard } from "@/lib/movie-types";
import {
  applyActionToDisplay,
  displayState,
  ratingAction,
  UNKNOWN_MOVIE_STATE,
  type MovieDisplayState,
  type MovieStateAction,
} from "@/lib/movie-state/actions";
import { movieStateAnnouncement } from "@/lib/movie-state/announce";
import { bffMovieStateClient, type MovieStateClient } from "@/lib/movie-state/client";
import { readCommittedStates } from "@/lib/movie-state/committed-store";
import { restoreFocus } from "@/lib/movie-state/focus";
import { RECOMMENDATIONS } from "@/lib/resources/definitions";
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

/** The in-flight frame for the one movie currently being written. */
type Optimistic = { movieId: number; state: MovieDisplayState };

/**
 * The interactive half of `/discover`.
 *
 * It is seeded with the server-loaded recommendation state rather than
 * fetching on mount, so the first movie is in the initial HTML. After that it
 * owns three things the server cannot: the optimistic frame of a mutation, the
 * canonical revision each committed response returns, and the re-read that
 * makes "Recommendations refreshed" true rather than decorative. That claim is
 * only ever made after the refetch resolves — a failed refetch says so.
 *
 * The recommendation contract carries no per-item state, which used to mean
 * every card started as "not saved" and a title already on the watchlist showed
 * an inviting `Watchlist` button until the first write came back `409`. Nothing
 * on the backend has changed; what changed is that the states other routes have
 * already committed are folded in from the tab-local relay before the cards
 * render, and the conflict path still corrects anything the relay does not
 * know. Per-card state reads are deliberately not the answer here: that is the
 * fan-out the local catalog snapshot exists to prevent.
 */
export function DiscoverExperience({
  userId,
  personaName,
  initialRecommendations,
  recordedEvidence,
  browseHref,
  quickPicksHref,
  movieHrefBase,
  movieHrefQuery = "",
  limit,
  stateClient = bffMovieStateClient,
}: {
  userId: number;
  personaName: string;
  initialRecommendations: ResourceState<RecommendationResponse>;
  recordedEvidence?: PreloadedTechnicalEvidence | null;
  browseHref: string;
  quickPicksHref: string;
  /**
   * Built from strings rather than a callback: this component is a client
   * boundary, and a server component cannot hand a function across it.
   */
  movieHrefBase: string;
  movieHrefQuery?: string;
  limit: number;
  stateClient?: MovieStateClient;
}) {
  const movieHrefFor = (movieId: number) =>
    `${movieHrefBase}/${movieId}${movieHrefQuery}`;
  const router = useRouter();
  const [recommendations, setRecommendations] = useState(initialRecommendations);
  /** Canonical records this route has learned, by movie. Never a guess. */
  const [known, setKnown] = useState<Record<number, MovieState>>({});
  const [optimistic, setOptimistic] = useState<Optimistic | null>(null);
  const [pendingMovieId, setPendingMovieId] = useState<number | null>(null);
  const [flow, setFlow] = useState<Flow>({ kind: "idle" });
  // Held as a snapshot rather than a lookup: a watched title leaves the ranked
  // set on the next request, and the rating panel has to outlive it.
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

  /**
   * Adopts a canonical record the API committed, wherever it came from: this
   * session's own write, the conflict re-read, or the relay another route left
   * behind. A record only wins if it is newer than what is already held, so an
   * older echo can never overwrite a fresher answer.
   */
  const adopt = useCallback((states: readonly MovieState[]) => {
    setKnown((current) => {
      let changed = false;
      const next = { ...current };
      for (const state of states) {
        const held = current[state.movie_id];
        if (held && held.revision >= state.revision) continue;
        next[state.movie_id] = state;
        changed = true;
      }
      return changed ? next : current;
    });
  }, []);

  // Reading the relay is an external read, not something derivable during
  // render: the server renders this component too and has no session storage,
  // so folding it in at render time would make the two disagree. It re-runs
  // when the ranked set changes, because a refreshed set may contain movies the
  // previous one did not.
  useEffect(() => {
    // Reading session storage and adopting what it holds is the effect's whole
    // job, and the rule's usual advice — derive it during render instead — is
    // the one thing that cannot work here: the server renders this component
    // too and has no session storage, so the two renders would disagree about
    // what the cards show. The state set here is the external read.
    if (typeof window === "undefined") return;
    const relayed = readCommittedStates(window.sessionStorage, userId);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (relayed.size) adopt([...relayed.values()]);
  }, [adopt, recommendations, userId]);

  const cardStates = useMemo(() => {
    const states: Record<number, MovieDisplayState> = {};
    for (const [movieId, state] of Object.entries(known)) {
      states[Number(movieId)] = displayState(state);
    }
    if (optimistic) states[optimistic.movieId] = optimistic.state;
    return states;
  }, [known, optimistic]);

  async function commit(
    movie: MovieCard,
    action: MovieStateAction,
    control: HTMLElement,
  ) {
    const currentState = cardStates[movie.id] ?? UNKNOWN_MOVIE_STATE;

    setPendingMovieId(movie.id);
    setFlow({ kind: "saving", message: `Saving ${movie.title}…` });
    setOptimistic({
      movieId: movie.id,
      state: applyActionToDisplay(currentState, action),
    });

    const result = await stateClient.mutate({
      userId,
      movieId: movie.id,
      resource: action.resource,
      method: action.method,
      rating:
        action.resource === "rating" && action.method === "PUT"
          ? action.rating
          : undefined,
      // `0` is the canonical "no state yet" assertion; anything else is a
      // revision the server issued, never one invented here.
      expectedRevision: known[movie.id]?.revision ?? 0,
    });

    if (result.status !== "committed") {
      // Dropping the pending frame *is* the rollback: the card falls back to
      // the last canonical record this route holds.
      setOptimistic(null);
      setPendingMovieId(null);
      setFlow({
        kind: "error",
        message: movieStateAnnouncement(
          result.status === "conflict"
            ? { kind: "conflict" }
            : { kind: "failed", failure: result.failure },
          { title: movie.title, voice: "discover" },
        ),
      });
      if (result.status === "conflict") {
        // Turn the conflict into a correction: read the canonical record so the
        // control shows the truth and the next click asserts a real revision.
        const canonical = await stateClient.readState(userId, movie.id);
        if (canonical) adopt([canonical]);
      }
      restoreFocus(control);
      return;
    }

    // The committed state replaces the optimistic guess outright; a
    // reconciliation that kept any local field would be a second source of
    // truth for the same movie.
    adopt([result.state]);
    setOptimistic(null);
    const message = movieStateAnnouncement(
      { kind: "committed", action },
      { title: movie.title, voice: "discover" },
    );
    if (result.state.watched_at !== null) {
      setJustWatched({ ...movie, state: displayState(result.state) });
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
    return (action: MovieStateAction, control: HTMLElement) => {
      void commit(movie, action, control);
    };
  }

  return (
    <ResourceRegion
      empty={
        <EmptyState
          action={
            <>
              <Link className="button-primary" href={browseHref}>
                Browse the catalog
              </Link>
              <QuickPicksEntry
                href={quickPicksHref}
                note="Or classify a handful quickly, so the next ranked set has something to work with."
              />
            </>
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
        const cards = recommendationCards(data.items, cardStates);
        const [featured, ...rest] = cards;
        const policy = describeServingPolicy(data);
        if (!featured) return null;
        return (
          <>
            <FeaturedMovie
              actions={
                <MovieStateControls
                  busy={pendingMovieId === featured.id}
                  controls={RECOMMENDATION_CONTROLS}
                  idPrefix={`featured-${featured.id}`}
                  onAction={onAction(featured)}
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
                  requestId={
                    hasResourceData(recommendations) ? recommendations.requestId : null
                  }
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
                  void commit(justWatched, ratingAction(value), control)
                }
                state={cardStates[justWatched.id] ?? justWatched.state}
              />
            ) : null}

            {rest.length ? (
              <MovieRail
                eyebrow={policy.label}
                footer={(movie) => (
                  <MovieStateControls
                    busy={pendingMovieId === movie.id}
                    compact
                    controls={RECOMMENDATION_CONTROLS}
                    idPrefix={`rail-${movie.id}`}
                    onAction={onAction(movie)}
                    state={movie.state}
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

            <QuickPicksEntry href={quickPicksHref} />
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
  state: MovieDisplayState;
  busy: boolean;
  onRate: (value: number | null, control: HTMLElement) => void;
}) {
  return (
    <section aria-labelledby="just-watched-heading" className="just-watched">
      <div>
        <p className="eyebrow">Just marked watched</p>
        <h2 className="section-title" id="just-watched-heading">
          Rate {movie.title}
        </h2>
      </div>
      <MovieRatingControl
        busy={busy}
        idPrefix={`just-watched-${movie.id}`}
        note="Stars are recorded feedback for this persona. The deployed recommender counts any rating as one observed watch, so a 1 and a 5 are the same learned signal today."
        onRate={onRate}
        rating={state.rating}
        showRecorded
        title={movie.title}
      />
    </section>
  );
}
