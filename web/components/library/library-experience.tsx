"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { LibraryRow, libraryRowAnchorId } from "@/components/library/library-row";
import {
  LIBRARY_SPOTLIGHT_ID,
  LibrarySpotlight,
  type SpotlightDetailReader,
} from "@/components/library/library-spotlight";
import {
  LibraryTabs,
  libraryPanelId,
  libraryTabId,
  libraryTabLabel,
} from "@/components/library/library-tabs";
import { TasteSummary } from "@/components/library/taste-summary";
import {
  ResourceLoading,
  ResourceRegion,
  resourceReasonDetail,
} from "@/components/ui/resource-region";
import { EmptyState } from "@/components/ui/resource-states";
import { useHydrated } from "@/lib/hydration";
import type { LibraryCounts, LibraryResponse, TasteSummaryResponse } from "@/lib/api";
import { BROWSE_GENRES } from "@/lib/browse/facets";
import { bffLibraryClient, type LibraryClient } from "@/lib/library/client";
import {
  affectsTasteSummary,
  appendLibraryPage,
  mapMovieState,
  movieMatchesTab,
  replaceMovieState,
} from "@/lib/library/collection";
import {
  EMPTY_SPOTLIGHT,
  isFirstSpotlight,
  isLastSpotlight,
  nextSpotlight,
  previousSpotlight,
  removeCurrent,
  shouldExtendSpotlight,
  syncToWindow,
  type SpotlightState,
} from "@/lib/library/spotlight";
import {
  applyActionToState,
  type MovieStateAction,
} from "@/lib/movie-state/actions";
import { movieStateAnnouncement } from "@/lib/movie-state/announce";
import { restoreFocus } from "@/lib/movie-state/focus";
import {
  newIdempotencyKey,
  type MovieStateMutationInput,
} from "@/lib/movie-state/mutate";
import { displayTitle } from "@/lib/movie-types";
import {
  LIBRARY_BASE_PATH,
  LIBRARY_PAGE_SIZE,
  LIBRARY_TABS,
  hasLibraryFilters,
  libraryHref,
  parseLibraryYear,
  libraryViewKey,
  nextLibraryUrlState,
  sortsForTab,
  type LibrarySort,
  type LibraryTab,
  type LibraryUrlState,
} from "@/lib/library/url-state";
import {
  emptyState,
  hasResourceData,
  isResourceFailure,
  loadingState,
  readyState,
  type ResourceFailure,
  type ResourceState,
} from "@/lib/resources/state";
import "./library.css";

const COLLECTION_TITLE: Record<LibraryTab, string> = {
  rated: "Titles with a star value",
  watchlist: "Saved for later",
  history: "Everything you have watched",
};

/**
 * What each collection means to the deployed system. These lines are the
 * difference between a library that documents itself and one that lets a
 * reviewer assume a star value is training data.
 *
 * Seen states the exclusion guarantee rather than implementing anything: the
 * serving filter (`watched-and-dismissed-excluded-v1`) is what keeps a watched
 * title out of Discover's featured slot and ranked rail, and this tab's job is
 * to say so where a reader is looking at the titles it applies to.
 */
const COLLECTION_MEANING: Record<LibraryTab, string> = {
  rated:
    "A star value is display feedback. Every watched title counts the same as one observed positive interaction.",
  watchlist:
    "Saving is organizational only: it seeds no candidates, excludes nothing, and changes no model features.",
  history:
    "Watched titles are the positive interactions candidate lookup reads from, and serving already excludes them from Discover's featured slot and ranked rail. A star value is display feedback and never a training signal.",
};

const SORT_LABEL: Record<LibrarySort, string> = {
  recent: "Most recent",
  title: "Title",
  rating: "Highest rated",
  release: "Newest release",
  tmdb: "Highest TMDB score",
};

const EMPTY_COPY: Record<LibraryTab, { title: string; message: string }> = {
  rated: {
    title: "No ratings yet",
    message:
      "Rate a movie this persona has watched and it collects here. Editing a star later never changes when it was watched.",
  },
  watchlist: {
    title: "Nothing saved yet",
    message:
      "Save an unwatched movie when you want to come back to it. Saving is organizational and changes no recommendation.",
  },
  history: {
    title: "No watched titles yet",
    message:
      "Marking a movie watched records the positive interaction this persona's recommendations are built from.",
  },
};

type TabView = { key: string; state: ResourceState<LibraryResponse> };

type PendingMutation = {
  movieId: number;
  title: string;
  action: MovieStateAction;
  request: MovieStateMutationInput;
  /** The control the reader used, held so focus can go back to it. */
  control: HTMLElement;
  /** Which surface asked, so focus falls back to the right second stop. */
  origin: "row" | "spotlight";
  /** The collection exactly as it stood before the optimistic write. */
  rollback: LibraryResponse;
};

/** Keeps the region's own request ID and source while swapping its rows. */
function withCollection(
  state: ResourceState<LibraryResponse>,
  data: LibraryResponse,
): ResourceState<LibraryResponse> {
  if (!hasResourceData(state)) return state;
  return data.items.length
    ? readyState("library", data, state.requestId, state.source)
    : emptyState("library", data, state.requestId, state.source);
}

function sameWindow(left: readonly number[], right: readonly number[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

export function LibraryExperience({
  actorName,
  personaLabel,
  personaResolved,
  initialUrlState,
  initialLibrary,
  initialTaste,
  client = bffLibraryClient,
  movieHref,
  basePath = LIBRARY_BASE_PATH,
  urlExtras,
  recordedNote,
}: {
  actorName: string;
  personaLabel: string;
  /** False when the persona directory could not be read; copy stays cautious. */
  personaResolved: boolean;
  initialUrlState: LibraryUrlState;
  initialLibrary: ResourceState<LibraryResponse>;
  initialTaste: ResourceState<TasteSummaryResponse>;
  client?: LibraryClient;
  movieHref?: (movieId: number) => string;
  /** Where this experience writes its URL state; `/library` for the live route. */
  basePath?: string;
  /** Extra query parameters the route needs preserved across navigation. */
  urlExtras?: Record<string, string>;
  /** Set only by the recorded preview, which advertises itself as recorded. */
  recordedNote?: string;
}) {
  const router = useRouter();
  const [urlState, setUrlState] = useState(initialUrlState);
  const [queryDraft, setQueryDraft] = useState(initialUrlState.query);
  const [yearFromDraft, setYearFromDraft] = useState(
    initialUrlState.yearFrom === null ? "" : String(initialUrlState.yearFrom),
  );
  const [yearToDraft, setYearToDraft] = useState(
    initialUrlState.yearTo === null ? "" : String(initialUrlState.yearTo),
  );
  const [views, setViews] = useState<Partial<Record<LibraryTab, TabView>>>(() => ({
    [initialUrlState.tab]: {
      key: libraryViewKey(initialUrlState),
      state: initialLibrary,
    },
  }));
  const [counts, setCounts] = useState<LibraryCounts | null>(
    hasResourceData(initialLibrary) ? initialLibrary.data.counts : null,
  );
  const [taste, setTaste] = useState(initialTaste);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pageFailure, setPageFailure] = useState<ResourceFailure | null>(null);
  const [staleCursor, setStaleCursor] = useState(false);
  const [busyMovieId, setBusyMovieId] = useState<number | null>(null);
  const [mutationFailure, setMutationFailure] = useState<ResourceFailure | null>(null);
  const [pending, setPending] = useState<PendingMutation | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [spotlight, setSpotlight] = useState<SpotlightState>(EMPTY_SPOTLIGHT);

  // The request each tab has already asked for. A ref because it gates an
  // effect that must not re-run when unrelated state changes, and because a
  // late response needs to know whether it has been superseded.
  const requested = useRef<Partial<Record<LibraryTab, string>>>({
    [initialUrlState.tab]: libraryViewKey(initialUrlState),
  });

  // Mirrors the view currently on screen for the adoption effect below.
  // Declared before it so the mirror is already current by the time that
  // effect runs in the same commit.
  const shownRef = useRef(urlState);
  useEffect(() => {
    shownRef.current = urlState;
  });

  /*
   * The URL owns this route's tab, sort, filter, and page position; the state
   * above is the local copy that lets a tab click paint its skeleton without
   * waiting for the router. The two agree for every edit made *here* — the copy
   * moves first and the URL follows it — but the route used to read the
   * server's parse exactly once, so anything that moved the URL from outside
   * was ignored: a browser Back left the reader on the collection they had just
   * navigated away from, with an address bar that disagreed.
   *
   * Adopting the server's parse rather than re-reading `useSearchParams` keeps
   * this to one source of truth, and it arrives with the first page that goes
   * with it — so a Back into a view this session has not loaded is served from
   * the server render instead of costing a second round trip.
   */
  const serverViewKey = libraryViewKey(initialUrlState);
  useEffect(() => {
    if (libraryViewKey(shownRef.current) === serverViewKey) return;
    // Only the adopted collection survives: keeping the other tabs' cached
    // pages while dropping the requests that produced them is what would leave
    // a tab claiming to be loaded with nothing to show.
    requested.current = { [initialUrlState.tab]: serverViewKey };
    setUrlState(initialUrlState);
    setQueryDraft(initialUrlState.query);
    setYearFromDraft(
      initialUrlState.yearFrom === null ? "" : String(initialUrlState.yearFrom),
    );
    setYearToDraft(initialUrlState.yearTo === null ? "" : String(initialUrlState.yearTo));
    setViews({ [initialUrlState.tab]: { key: serverViewKey, state: initialLibrary } });
    // A failed server read still names the right view; it just cannot restate
    // the counts, so the tabs keep the last numbers they were given.
    setCounts((current) =>
      hasResourceData(initialLibrary) ? initialLibrary.data.counts : current,
    );
    // The payload is read from the render that moved the key, which is the one
    // carrying the page for it. Listing it as a dependency would re-run this on
    // every server re-render and throw away an accumulated window.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverViewKey]);

  // Where this route writes its own URL. Cheap enough to rebuild per render,
  // and it is only ever called from an event handler.
  const href = (state: LibraryUrlState) => libraryHref(state, basePath, urlExtras);

  /*
   * The URL write, mirrored in a ref.
   *
   * The load callback below rewrites the address after dropping a stale cursor,
   * and it must not take the router or this component's props into its
   * dependency list to do it: a callback that changes identity every render
   * re-runs the load effect every render, and the one commit where that effect
   * disagrees with the state it is about to adopt would issue a second read for
   * a collection the reader has already navigated away from.
   */
  const replaceUrl = useRef((state: LibraryUrlState) => {
    router.replace(href(state), { scroll: false });
  });
  useEffect(() => {
    replaceUrl.current = (state: LibraryUrlState) => {
      router.replace(href(state), { scroll: false });
    };
  });

  const { tab, sort, query, genre, cursor, userId } = urlState;
  const viewKey = libraryViewKey(urlState);
  const view = views[tab];
  const tabLabel = libraryTabLabel(tab);
  const collection = view && hasResourceData(view.state) ? view.state.data : null;
  const filtered = hasLibraryFilters(urlState);

  const setView = useCallback((target: LibraryTab, next: TabView) => {
    setViews((current) => ({ ...current, [target]: next }));
  }, []);

  const readPage = useCallback(
    (state: LibraryUrlState, limit = LIBRARY_PAGE_SIZE) =>
      client.readLibrary({
        userId: state.userId,
        tab: state.tab,
        sort: state.sort,
        query: state.query,
        genre: state.genre,
        yearFrom: state.yearFrom,
        yearTo: state.yearTo,
        cursor: state.cursor,
        limit,
      }),
    [client],
  );

  const loadFirstPage = useCallback(
    async (target: LibraryUrlState, options: { restarted?: boolean } = {}) => {
      const key = libraryViewKey(target);
      requested.current[target.tab] = key;
      setView(target.tab, { key, state: loadingState("library") });
      setPageFailure(null);
      setStaleCursor(Boolean(options.restarted));

      const next = await readPage(target);
      // A newer request for the same tab may have started while this one was
      // in flight; the newest view wins.
      if (requested.current[target.tab] !== key) return;
      setView(target.tab, { key, state: next });
      if (hasResourceData(next)) setCounts(next.data.counts);
    },
    [readPage, setView],
  );

  // Each tab is its own read. A failure in one leaves the other two exactly as
  // they were, which is the point of loading them separately.
  useEffect(() => {
    if (requested.current[urlState.tab] === viewKey) return;
    void loadFirstPage(urlState);
  }, [viewKey, urlState, loadFirstPage]);

  /*
   * A page position that expired, rather than an outage.
   *
   * The API binds a cursor to the fingerprint of the tab, sort and filters it
   * was issued under, so a link somebody kept from before any of those changed
   * earns a `400`. The view itself still exists, so it is reloaded from the top
   * behind a plain notice instead of being shown as a failed collection.
   *
   * Keyed on the failed state rather than done inside the read, because the
   * server renders the first page too: a stale link opened cold arrives here as
   * a failure that no client read produced, and it has to recover the same way.
   */
  const staleCursorRejected =
    Boolean(cursor) &&
    view !== undefined &&
    isResourceFailure(view.state) &&
    view.state.reason === "bad-request";
  useEffect(() => {
    if (!staleCursorRejected) return;
    const restarted = { ...shownRef.current, cursor: null };
    setUrlState(restarted);
    replaceUrl.current(restarted);
    void loadFirstPage(restarted, { restarted: true });
  }, [staleCursorRejected, loadFirstPage]);

  /*
   * The window the Seen spotlight walks: the loaded rows that still belong to
   * this collection, in the order the list shows them. A row that has left the
   * collection stays on screen until the view reloads — it says so — but the
   * spotlight is a single-title presentation and cannot honestly feature a
   * title the reader has just removed.
   */
  const spotlightIds = useMemo(() => {
    if (tab !== "history" || !collection) return [];
    return collection.items
      .filter((movie) => movieMatchesTab(movie.state, "history"))
      .map((movie) => movie.movie_id);
  }, [collection, tab]);

  // Adjusted during render rather than in an effect, so the spotlight and the
  // rows below it are never one frame out of step with each other.
  if (!sameWindow(spotlight.movieIds, spotlightIds)) {
    setSpotlight((current) => syncToWindow(current, spotlightIds));
  }

  function navigate(change: Parameters<typeof nextLibraryUrlState>[1]) {
    const next = nextLibraryUrlState(urlState, change);
    setUrlState(next);
    setQueryDraft(next.query);
    setYearFromDraft(next.yearFrom === null ? "" : String(next.yearFrom));
    setYearToDraft(next.yearTo === null ? "" : String(next.yearTo));
    router.replace(href(next), { scroll: false });
  }

  async function loadMore() {
    if (!view || !hasResourceData(view.state)) return;
    const nextCursor = view.state.data.page.next_cursor;
    if (!nextCursor) return;

    const loaded = view.state.data;
    setLoadingMore(true);
    setPageFailure(null);
    const next = await readPage({ ...urlState, cursor: nextCursor });
    setLoadingMore(false);

    if (isResourceFailure(next)) {
      // Everything already read stays on screen; only the append failed.
      setPageFailure(next);
      return;
    }
    if (!hasResourceData(next)) return;

    const advanced = { ...urlState, cursor: nextCursor };
    const advancedKey = libraryViewKey(advanced);
    requested.current[tab] = advancedKey;
    setView(tab, {
      key: advancedKey,
      state: withCollection(next, appendLibraryPage(loaded, next.data)),
    });
    setCounts(next.data.counts);
    setUrlState(advanced);
    router.replace(href(advanced), { scroll: false });
  }

  /*
   * The spotlight extends through the same cursor the list's button uses: one
   * window, one cursor, appended in one place. A failed append stops it until
   * the reader retries, so a dead endpoint cannot be asked once per render.
   */
  const extendForSpotlight =
    tab === "history" &&
    Boolean(collection?.page.has_more) &&
    !loadingMore &&
    !pageFailure &&
    spotlightIds.length > 0 &&
    shouldExtendSpotlight(spotlight);
  const loadMoreRef = useRef(loadMore);
  useEffect(() => {
    loadMoreRef.current = loadMore;
  });
  useEffect(() => {
    if (!extendForSpotlight) return;
    void loadMoreRef.current();
  }, [extendForSpotlight]);

  /**
   * Counts and the ratings summary are computed by the API, so they are read
   * back rather than recalculated here. The list itself is not refetched: the
   * committed state is already reconciled into the row, and refetching would
   * discard every appended page and pull the row the reader just acted on out
   * from under them.
   */
  const refreshDerivedState = useCallback(
    async (action: MovieStateAction, from: LibraryUrlState) => {
      for (const other of LIBRARY_TABS) {
        if (other !== from.tab) requested.current[other] = undefined;
      }
      setViews((current) => ({ [from.tab]: current[from.tab] }));

      const [refreshed, summary] = await Promise.all([
        // One row is enough: only the counts are being read back.
        readPage({ ...from, cursor: null }, 1),
        affectsTasteSummary(action)
          ? client.readTasteProfile(from.userId)
          : Promise.resolve(null),
      ]);
      if (hasResourceData(refreshed)) setCounts(refreshed.data.counts);
      if (summary && hasResourceData(summary)) setTaste(summary);
    },
    [client, readPage],
  );

  const runMutation = useCallback(
    async (mutation: PendingMutation, from: LibraryUrlState, base: TabView) => {
      setBusyMovieId(mutation.movieId);
      setMutationFailure(null);
      setPending(mutation);

      const now = new Date().toISOString();
      setView(from.tab, {
        key: base.key,
        state: withCollection(
          base.state,
          mapMovieState(mutation.rollback, mutation.movieId, (state) =>
            applyActionToState(state, mutation.action, now),
          ),
        ),
      });

      const voice = { title: mutation.title, voice: "library" as const, persona: personaLabel };
      const result = await client.mutate(mutation.request);
      let settled = mutation.rollback;

      if (result.status === "committed") {
        settled = replaceMovieState(mutation.rollback, mutation.movieId, result.state);
        // A title that has left this collection leaves the spotlight with it,
        // so the announcement and the focus walk below both name what is
        // current now rather than a movie on its way out.
        if (!movieMatchesTab(result.state, from.tab)) {
          setSpotlight((current) => removeCurrent(current, mutation.movieId));
        }
        setAnnouncement(
          movieStateAnnouncement({ kind: "committed", action: mutation.action }, voice),
        );
        setPending(null);
      } else if (result.status === "conflict") {
        // The row was written from a revision that is no longer current. Show
        // what is actually stored rather than guessing which side won.
        const canonical = await client.readState(from.userId, mutation.movieId);
        if (canonical) {
          settled = replaceMovieState(mutation.rollback, mutation.movieId, canonical);
        }
        setAnnouncement(movieStateAnnouncement({ kind: "conflict" }, voice));
        setPending(null);
      } else if (result.status === "refused") {
        // A rule, not a race: the row is already back on `mutation.rollback`,
        // nothing was stored to re-read, and `Try again` is dropped because a
        // second press would ask the same rule the same question.
        setAnnouncement(
          movieStateAnnouncement({ kind: "refused", detail: result.detail }, voice),
        );
        setPending(null);
      } else {
        setMutationFailure(result.failure);
        setAnnouncement(
          movieStateAnnouncement({ kind: "failed", failure: result.failure }, voice),
        );
      }

      setView(from.tab, { key: base.key, state: withCollection(base.state, settled) });
      setBusyMovieId(null);
      // The control the reader used, then the surface it belongs to, then the
      // collection. A spotlight control can outlive its own movie, so its
      // second stop is the section rather than the row that is leaving.
      restoreFocus(
        mutation.control,
        mutation.origin === "spotlight"
          ? LIBRARY_SPOTLIGHT_ID
          : libraryRowAnchorId(mutation.movieId),
        libraryTabId(from.tab),
      );

      if (result.status === "committed") {
        await refreshDerivedState(mutation.action, from);
      }
    },
    [client, personaLabel, refreshDerivedState, setView],
  );

  function act(
    movieId: number,
    title: string,
    revision: number,
    origin: PendingMutation["origin"] = "row",
  ) {
    return (action: MovieStateAction, control: HTMLElement) => {
      if (!view || !hasResourceData(view.state)) return;
      void runMutation(
        {
          movieId,
          title,
          action,
          control,
          origin,
          rollback: view.state.data,
          request: {
            userId,
            movieId,
            resource: action.resource,
            method: action.method,
            rating:
              action.resource === "rating" && action.method === "PUT"
                ? action.rating
                : undefined,
            expectedRevision: revision,
            // One key per intent: `Try again` replays the original commit
            // instead of writing a second feedback event.
            idempotencyKey: newIdempotencyKey(),
          },
        },
        urlState,
        view,
      );
    };
  }

  const validSorts = useMemo(() => sortsForTab(tab), [tab]);
  const detailHref =
    movieHref ??
    ((movieId: number) =>
      `/movies/${movieId}?user=${userId}&returnTo=${encodeURIComponent(href(urlState))}`);

  const readSpotlightDetail = useCallback<SpotlightDetailReader>(
    (movieId, signal) => client.readMovieDetail(userId, movieId, { signal }),
    [client, userId],
  );

  /*
   * A filtered collection with no rows is not an empty one. The distinction is
   * the whole difference between "you have watched nothing" and "nothing here
   * matches", and only the second one has a way out that is not Browse.
   */
  const emptyMessage = !filtered
    ? EMPTY_COPY[tab].message
    : tab === "history" || !query
      ? `Nothing in ${tabLabel} matches these filters.`
      : `Nothing in ${tabLabel.toLowerCase()} matches \u201C${query}\u201D.`;

  const spotlightMovie =
    tab === "history" && collection
      ? (collection.items.find(
          (movie) => movie.movie_id === spotlight.movieIds[spotlight.index],
        ) ?? null)
      : null;
  // `matched` is the exact number of rows this query has, which is the number
  // the readout would otherwise have to invent from the window it has loaded.
  // The fallback is only for the moment before the first page arrives.
  const spotlightTotal = collection?.page.matched ?? spotlight.movieIds.length;
  // Every control on this route is real markup before React attaches and does
  // nothing until it has; the attribute is what the browser tests wait for.
  const interactive = useHydrated();

  return (
    <div className="library-route" data-interactive={String(interactive)}>
      {/* No `Exploring as {persona}` eyebrow here: the shell header prints that
          exact sentence about 40px above this block, and the design contract
          asks for one labelled persona rather than the same claim twice. The
          lede below still says whose record this is, which is the part the
          shell cannot say — that the signed-in actor and the persona are two
          different identities. */}
      <header className="library-intro">
        <h1 className="display-title library-title" id="library-title" tabIndex={-1}>
          A record of what moved you.
        </h1>
        <p className="muted library-lede">
          This is the selected demo persona&apos;s live state
          {personaResolved ? "" : " (the persona directory was unavailable, so its ID is shown)"}
          . It is not the signed-in actor&apos;s private library: {actorName} is
          signed in and acting on {personaLabel}&apos;s behalf.
        </p>
        {recordedNote ? <p className="library-recorded">{recordedNote}</p> : null}
      </header>

      <section aria-labelledby="library-collection-title" className="library-shell">
        <LibraryTabs
          active={tab}
          counts={counts}
          onSelect={(next) => navigate({ tab: next })}
        />

        <div
          aria-labelledby={libraryTabId(tab)}
          className="library-panel"
          id={libraryPanelId(tab)}
          role="tabpanel"
        >
          <header className="library-collection-heading">
            <div>
              <p className="eyebrow">{tabLabel} collection</p>
              <h2 className="section-title" id="library-collection-title">
                {COLLECTION_TITLE[tab]}
              </h2>
            </div>
            <p className="library-collection-meaning">{COLLECTION_MEANING[tab]}</p>
          </header>

          <div className="library-controls">
            <form
              className="library-filter"
              onSubmit={(event) => {
                event.preventDefault();
                navigate({
                  query: queryDraft,
                  ...(tab === "history"
                    ? {
                        yearFrom: parseLibraryYear(yearFromDraft),
                        yearTo: parseLibraryYear(yearToDraft),
                      }
                    : {}),
                });
              }}
            >
              <label className="visually-hidden" htmlFor="library-search">
                Filter {tabLabel.toLowerCase()} by title
              </label>
              <input
                className="library-input library-search"
                id="library-search"
                onChange={(event) => setQueryDraft(event.target.value)}
                placeholder={`Filter ${tabLabel.toLowerCase()} by title`}
                type="search"
                value={queryDraft}
              />
              {/* The year bounds are typed rather than chosen, so they are
                  committed with the search rather than on every keystroke: a
                  half-typed "19" is a filter nobody asked for. */}
              {tab === "history" ? (
                <span className="library-years">
                  <label className="library-year" htmlFor="library-year-from">
                    From year
                    <input
                      className="library-input library-year-input"
                      id="library-year-from"
                      inputMode="numeric"
                      maxLength={4}
                      onChange={(event) => setYearFromDraft(event.target.value)}
                      value={yearFromDraft}
                    />
                  </label>
                  <label className="library-year" htmlFor="library-year-to">
                    To year
                    <input
                      className="library-input library-year-input"
                      id="library-year-to"
                      inputMode="numeric"
                      maxLength={4}
                      onChange={(event) => setYearToDraft(event.target.value)}
                      value={yearToDraft}
                    />
                  </label>
                </span>
              ) : null}
              <button className="button-secondary library-action" type="submit">
                Filter
              </button>
            </form>

            {tab === "history" ? (
              <label className="library-sort" htmlFor="library-genre">
                Genre
                <select
                  className="library-select"
                  id="library-genre"
                  onChange={(event) => navigate({ genre: event.target.value || null })}
                  value={genre ?? ""}
                >
                  <option value="">All genres</option>
                  {BROWSE_GENRES.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            <label className="library-sort" htmlFor="library-sort">
              Sort
              <select
                className="library-select"
                id="library-sort"
                onChange={(event) => navigate({ sort: event.target.value as LibrarySort })}
                value={sort}
              >
                {validSorts.map((option) => (
                  <option key={option} value={option}>
                    {SORT_LABEL[option]}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p aria-live="polite" className="visually-hidden">
            {announcement}
          </p>

          {mutationFailure ? (
            <LibraryProblem
              failure={mutationFailure}
              onDismiss={() => setMutationFailure(null)}
              onRetry={
                pending && mutationFailure.retryable
                  ? () => void runMutation(pending, urlState, view as TabView)
                  : undefined
              }
            />
          ) : null}

          {staleCursor ? (
            <p className="library-continued" role="status">
              That page link no longer matches this view, so the list starts from
              the beginning.
            </p>
          ) : null}

          {cursor ? (
            <p className="library-continued">
              Continued from an earlier page of this collection.{" "}
              <button
                className="library-link-button"
                onClick={() => navigate({ cursor: null })}
                type="button"
              >
                Back to the first page
              </button>
            </p>
          ) : null}

          <ResourceRegion
            empty={
              <EmptyState
                action={
                  filtered ? (
                    <button
                      className="button-secondary"
                      onClick={() =>
                        navigate({ query: "", genre: null, yearFrom: null, yearTo: null })
                      }
                      type="button"
                    >
                      {tab === "history" ? "Clear filters" : "Clear the filter"}
                    </button>
                  ) : (
                    <Link className="button-secondary" href="/browse">
                      Browse the catalog
                    </Link>
                  )
                }
                message={emptyMessage}
                title={filtered ? "No matches in this collection" : EMPTY_COPY[tab].title}
              />
            }
            label={`${tabLabel} collection`}
            loading={
              <ResourceLoading label={`the ${tabLabel.toLowerCase()} collection`} lines={4} />
            }
            onRetry={() => void loadFirstPage(urlState)}
            state={view?.state ?? loadingState("library")}
          >
            {(loaded) => (
              <>
                {spotlightMovie ? (
                  <LibrarySpotlight
                    busy={busyMovieId !== null}
                    hasNext={!isLastSpotlight(spotlight)}
                    hasPrevious={!isFirstSpotlight(spotlight)}
                    href={detailHref(spotlightMovie.movie_id)}
                    movie={spotlightMovie}
                    onAction={act(
                      spotlightMovie.movie_id,
                      displayTitle(
                        spotlightMovie.title,
                        spotlightMovie.release_year ?? null,
                      ),
                      spotlightMovie.state.revision,
                      "spotlight",
                    )}
                    onNext={() => setSpotlight(nextSpotlight)}
                    onPrevious={() => setSpotlight(previousSpotlight)}
                    persona={personaLabel}
                    position={spotlight.index + 1}
                    readDetail={readSpotlightDetail}
                    total={spotlightTotal}
                  />
                ) : null}

                <ul aria-label={`${tabLabel} movies`} className="library-list">
                  {loaded.items.map((movie) => (
                    <LibraryRow
                      busy={busyMovieId === movie.movie_id}
                      // One write at a time keeps revisions predictable and stops a
                      // second click landing while the first is in flight.
                      disabled={busyMovieId !== null && busyMovieId !== movie.movie_id}
                      href={detailHref(movie.movie_id)}
                      key={movie.movie_id}
                      movie={movie}
                      onAction={act(
                        movie.movie_id,
                        // The announcement names the movie the row shows, not the
                        // raw catalog string: "Babe (1995) is now watched" reads
                        // like a database dump being spoken aloud.
                        displayTitle(movie.title, movie.release_year ?? null),
                        movie.state.revision,
                      )}
                      persona={personaLabel}
                      tab={tab}
                    />
                  ))}
                </ul>
              </>
            )}
          </ResourceRegion>

          {pageFailure ? (
            <LibraryProblem
              failure={pageFailure}
              headline="The next page could not be loaded"
              onDismiss={() => setPageFailure(null)}
              onRetry={() => void loadMore()}
            />
          ) : null}

          {collection?.page.has_more ? (
            <div className="library-more">
              <button
                className="button-secondary"
                disabled={loadingMore}
                onClick={() => void loadMore()}
                type="button"
              >
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          ) : null}
        </div>
      </section>

      <section aria-labelledby="taste-summary-title" className="library-taste">
        <ResourceRegion
          empty={
            <EmptyState
              message="This persona has no ratings yet, so there is nothing to summarize."
              title="No ratings to summarize"
            />
          }
          label="Live ratings summary"
          state={taste}
        >
          {(summary) => <TasteSummary summary={summary} />}
        </ResourceRegion>
      </section>
    </div>
  );
}

/**
 * A failed write is not a failed region: the collection is still on screen and
 * still correct, so this reports the write on its own terms while reusing the
 * shared cause wording.
 */
function LibraryProblem({
  failure,
  headline = "That change was not saved",
  onRetry,
  onDismiss,
}: {
  failure: ResourceFailure;
  headline?: string;
  onRetry?: () => void;
  onDismiss: () => void;
}) {
  return (
    <section className="resource-state resource-error library-problem" role="alert">
      <span aria-hidden="true" className="resource-state-icon">
        !
      </span>
      <div>
        <p className="resource-state-title">
          {failure.status === "auth-expired" ? "Your session expired" : headline}
        </p>
        <p>{resourceReasonDetail(failure)}</p>
      </div>
      <div className="resource-state-actions">
        {failure.status === "auth-expired" ? (
          <Link className="button-primary" href="/">
            Sign in again
          </Link>
        ) : null}
        {onRetry ? (
          <button className="button-secondary" onClick={onRetry} type="button">
            Try again
          </button>
        ) : null}
        <button className="button-quiet" onClick={onDismiss} type="button">
          Dismiss
        </button>
      </div>
      <p className="resource-state-meta">Request {failure.requestId}</p>
    </section>
  );
}
