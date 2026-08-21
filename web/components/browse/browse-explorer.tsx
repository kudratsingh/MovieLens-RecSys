"use client";

/**
 * Browse: URL-owned query state over the cursor-paginated catalog contract.
 *
 * The data flow is deliberately one-directional. The URL is the only source of
 * truth for what is being asked for; a control edit rewrites the URL, and the
 * URL drives the fetch. That is what keeps a deep link, a filter click, and a
 * restored session from drifting into three different answers.
 *
 * The endpoint's cursor is bound to the search/filter/sort fingerprint it was
 * issued under, so the load effect keys off the *filter* identity and not the
 * whole query string: paging deeper must not restart the window, and changing
 * a filter must. `withBrowseFilters` drops the cursor at every filter edit for
 * the same reason, so a stale cursor is not normally reachable — and when one
 * arrives anyway from an old link, the 400 is handled as what it is, a query
 * that moved, not an outage.
 *
 * What the URL cannot carry is the accumulated window: page four is only
 * reachable by having asked for one through three. So the URL holds the resume
 * point — the cursor for the most recently loaded page — and the full window
 * plus scroll position is kept per tab in `sessionStorage`, which is what
 * makes returning from a movie land where the viewer left.
 */

import { useRouter, useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { BrowseControls } from "@/components/browse/browse-controls";
import { CatalogGrid } from "@/components/browse/catalog-grid";
import { ResourceProblem } from "@/components/ui/resource-region";
import { EmptyState } from "@/components/ui/resource-states";
import type { CatalogItem, MovieState } from "@/lib/api";
import {
  applyCommittedStates,
  readCommittedStates,
} from "@/lib/movie-state/committed-store";
import {
  browseHref,
  browseFilterKey,
  DEFAULT_BROWSE_QUERY,
  catalogRequestParams,
  hasActiveBrowseFilters,
  parseBrowseQuery,
  withBrowseFilters,
  type BrowseQuery,
} from "@/lib/browse/query";
import {
  browseSnapshotKey,
  readBrowseSnapshot,
  saveBrowseSnapshot,
  snapshotFromWindow,
  windowFromSnapshot,
} from "@/lib/browse/restoration";
import {
  appendCatalogPage,
  restartAfterStaleCursor,
  replaceMovieState,
  startWindow,
  type CatalogWindow,
} from "@/lib/browse/window";
import { CATALOG } from "@/lib/resources/definitions";
import { readBffResource } from "@/lib/resources/browser";
import {
  hasResourceData,
  isResourceFailure,
  type ResourceFailure,
} from "@/lib/resources/state";
import "./browse-explorer.css";

/**
 * Scroll restoration has to run after the grid is in the DOM but before the
 * browser paints, or it scrolls a document that is still one row tall. This
 * component is server-rendered too, where a layout effect does nothing and
 * React says so, hence the swap.
 */
const useRestorationEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

type RequestState =
  | { kind: "idle" }
  | { kind: "loading"; append: boolean }
  | { kind: "failed"; append: boolean; failure: ResourceFailure };

export type BrowseExplorerProps = {
  userId: number;
  /** Same-origin BFF endpoint answering the catalog contract. */
  catalogEndpoint: string;
  /** Route the query state is serialized into. */
  browsePath: string;
  /** Route prefix for a movie detail link. */
  movieBasePath: string;
  /**
   * Route parameters that are not part of the catalog query but must survive
   * every filter edit and detail hop — the selected demo persona above all.
   */
  persistedParams?: Record<string, string>;
};

export function BrowseExplorer({
  userId,
  catalogEndpoint,
  browsePath,
  movieBasePath,
  persistedParams,
}: BrowseExplorerProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = useMemo(() => parseBrowseQuery(searchParams), [searchParams]);
  const filterKey = browseFilterKey(query);
  // Held by content rather than by reference: the route passes an object
  // literal, and a re-rendered server payload would otherwise hand this
  // component a new identity and make the load effect refetch for nothing.
  const persistedKey = new URLSearchParams(persistedParams).toString();
  const persisted = useMemo(
    () => Object.fromEntries(new URLSearchParams(persistedKey)),
    [persistedKey],
  );

  const [movieWindow, setMovieWindow] = useState<CatalogWindow>(() =>
    startWindow(filterKey, query.cursor),
  );
  const [request, setRequest] = useState<RequestState>({
    kind: "loading",
    append: false,
  });

  // Read inside effects and callbacks so neither closes over a stale query.
  // Declared before every other effect in this component so the mirrors are
  // already current by the time the load effect below runs.
  const queryRef = useRef(query);
  const windowRef = useRef(movieWindow);
  const sequenceRef = useRef(0);
  const pendingScrollRef = useRef<number | null>(null);
  useEffect(() => {
    queryRef.current = query;
    windowRef.current = movieWindow;
  });

  const saveSnapshot = useCallback(() => {
    if (typeof window === "undefined") return;
    const current = windowRef.current;
    if (!current.items.length) return;
    saveBrowseSnapshot(
      window.sessionStorage,
      browseSnapshotKey(userId, current.filterKey),
      snapshotFromWindow(current, { scrollY: window.scrollY }),
    );
  }, [userId]);

  const runRequest = useCallback(
    async (input: { filterKey: string; cursor: string | null; append: boolean }) => {
      let cursor = input.cursor;
      let append = input.append;
      let droppedStaleCursor = false;

      // At most two attempts: the second only happens when the endpoint tells
      // us the cursor no longer belongs to this query.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const sequence = (sequenceRef.current += 1);
        setRequest({ kind: "loading", append });

        const params = catalogRequestParams(queryRef.current, { cursor });
        const state = await readBffResource(
          CATALOG,
          withQuery(catalogEndpoint, params),
        );
        if (sequence !== sequenceRef.current) return;

        if (hasResourceData(state)) {
          const startedAt = cursor;
          const restarted = droppedStaleCursor;
          setMovieWindow((current) => {
            const base =
              append && current.filterKey === input.filterKey
                ? current
                : {
                    ...startWindow(input.filterKey, startedAt),
                    restartedFromStaleCursor: restarted,
                  };
            return appendCatalogPage(base, state.data);
          });
          setRequest({ kind: "idle" });
          syncCursorInUrl(browsePath, queryRef.current, startedAt, persisted);
          return;
        }

        if (
          isResourceFailure(state) &&
          cursor !== null &&
          (state.httpStatus === 400 || state.httpStatus === 422)
        ) {
          // The cursor outlived its query. Start the same filter set again from
          // the top rather than showing an error where results belong.
          cursor = null;
          append = false;
          droppedStaleCursor = true;
          setMovieWindow((current) => restartAfterStaleCursor(current));
          continue;
        }

        if (isResourceFailure(state)) {
          setRequest({ kind: "failed", append: input.append, failure: state });
        }
        return;
      }
    },
    [browsePath, catalogEndpoint, persisted],
  );

  /**
   * Adopts a stored window for this filter set, if there is one.
   *
   * It has to happen after hydration rather than in a state initializer: the
   * server renders this component too and has no `sessionStorage`, so reading
   * it during the first render would make the client and the server disagree
   * about what the grid contains.
   */
  const restoreWindow = useCallback(
    (targetFilterKey: string): boolean => {
      if (typeof window === "undefined") return false;
      const snapshot = readBrowseSnapshot(
        window.sessionStorage,
        browseSnapshotKey(userId, targetFilterKey),
        targetFilterKey,
      );
      if (!snapshot) return false;

      // A restored window predates anything committed since it was written, so
      // fold in the canonical states the detail route relayed back.
      const relayed = readCommittedStates(window.sessionStorage, userId);
      const base = windowFromSnapshot(snapshot);
      sequenceRef.current += 1;
      setMovieWindow({
        ...base,
        items: applyCommittedStates(base.items, relayed),
      });
      setRequest({ kind: "idle" });

      // Applied by the layout effect below, once the restored grid has actually
      // been committed and the document is tall enough to hold the offset.
      pendingScrollRef.current = snapshot.scrollY;
      return true;
    },
    [userId],
  );

  useEffect(() => {
    // Reading `sessionStorage` and adopting what it holds is the effect's whole
    // job, and the rule's usual advice — derive it during render instead — is
    // the one thing that cannot work here: the server renders this component
    // too and has no session storage, so the two renders would disagree about
    // what the grid contains. The state set here is the external read, not a
    // value derivable from props.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (restoreWindow(filterKey)) return;
    void runRequest({
      filterKey,
      cursor: queryRef.current.cursor,
      append: false,
    });
  }, [filterKey, restoreWindow, runRequest]);

  useRestorationEffect(() => {
    const target = pendingScrollRef.current;
    if (target === null || movieWindow.items.length === 0) return;
    pendingScrollRef.current = null;
    window.scrollTo({ top: target, behavior: "instant" });
    // Reserved poster boxes settle within a frame, but the router does its own
    // scroll work on a back navigation; re-applying once wins that race.
    requestAnimationFrame(() =>
      window.scrollTo({ top: target, behavior: "instant" }),
    );
  }, [movieWindow.items.length]);

  useEffect(() => {
    // `pagehide` covers a reload or a full navigation away; the grid's click
    // handler covers the in-app hop to a movie.
    const onPageHide = () => saveSnapshot();
    window.addEventListener("pagehide", onPageHide);
    return () => window.removeEventListener("pagehide", onPageHide);
  }, [saveSnapshot]);

  const applyFilters = useCallback(
    (patch: Parameters<typeof withBrowseFilters>[1]) => {
      router.replace(
        browseHref(
          browsePath,
          withBrowseFilters(queryRef.current, patch),
          persisted,
        ),
        { scroll: false },
      );
    },
    [browsePath, persisted, router],
  );

  const onCommitted = useCallback((state: MovieState) => {
    setMovieWindow((current) => replaceMovieState(current, state.movie_id, state));
  }, []);

  const hrefFor = useCallback(
    (item: CatalogItem) => {
      const params = new URLSearchParams(persisted);
      params.set("returnTo", browseHref(browsePath, queryRef.current, persisted));
      return `${movieBasePath}/${item.movie_id}?${params.toString()}`;
    },
    [browsePath, movieBasePath, persisted],
  );

  // "Clear filters" returns to the default cut, not to a bare path: the
  // persona parameter is not a filter and must survive the reset.
  const clearedHref = browseHref(browsePath, DEFAULT_BROWSE_QUERY, persisted);
  const items = movieWindow.items;
  const loadingFirstPage = request.kind === "loading" && !request.append;
  const failedFirstPage = request.kind === "failed" && !request.append;

  return (
    <>
      <BrowseControls
        hasFilters={hasActiveBrowseFilters(query)}
        onChange={applyFilters}
        onClear={() => router.replace(clearedHref, { scroll: false })}
        query={query}
      />

      <BrowseStatus
        catalogWindow={movieWindow}
        items={items.length}
        loading={loadingFirstPage}
        onStartOver={() =>
          router.replace(
            browseHref(
              browsePath,
              { ...queryRef.current, cursor: null },
              persisted,
            ),
            { scroll: false },
          )
        }
      />

      {items.length ? (
        <div onClickCapture={saveSnapshot}>
          <CatalogGrid
            hrefFor={hrefFor}
            items={items}
            label="Browse results"
            onCommitted={onCommitted}
            userId={userId}
          />
        </div>
      ) : null}

      {loadingFirstPage ? <CatalogGridSkeleton /> : null}

      {!items.length && request.kind === "idle" ? (
        <EmptyState
          action={
            <button
              className="button-secondary"
              onClick={() => router.replace(clearedHref, { scroll: false })}
              type="button"
            >
              Clear search and filters
            </button>
          }
          message="Try a different title, a wider decade, or remove the genre filter."
          title="No movies match this cut"
        />
      ) : null}

      {request.kind === "failed" ? (
        <div className="browse-problem">
          <ResourceProblem
            failure={request.failure}
            label={failedFirstPage ? "Catalog" : "The next page"}
            onRetry={() =>
              void runRequest({
                filterKey,
                cursor: request.append ? movieWindow.nextCursor : queryRef.current.cursor,
                append: request.append,
              })
            }
          />
        </div>
      ) : null}

      {movieWindow.hasMore && movieWindow.nextCursor ? (
        <div className="browse-more">
          <button
            // `aria-disabled` rather than `disabled`: a keyboard viewer who
            // presses this must still have something focused while it loads.
            aria-busy={request.kind === "loading"}
            aria-disabled={request.kind === "loading"}
            className="button-secondary"
            onClick={() => {
              if (request.kind === "loading") return;
              void runRequest({
                filterKey,
                cursor: movieWindow.nextCursor,
                append: true,
              });
            }}
            type="button"
          >
            {request.kind === "loading" && request.append
              ? "Loading more…"
              : "Load more movies"}
          </button>
        </div>
      ) : null}

      {!movieWindow.hasMore && items.length ? (
        <p className="browse-end">
          {movieWindow.resumedFrom
            ? "That is the end of this continuation."
            : "That is every title matching these filters."}
        </p>
      ) : null}
    </>
  );
}

/**
 * The result line. It counts what has been loaded and says whether more
 * exists — it never claims a total, because the endpoint does not return one
 * and a guessed total is the kind of small lie that costs a reviewer's trust.
 */
function BrowseStatus({
  items,
  loading,
  catalogWindow,
  onStartOver,
}: {
  items: number;
  loading: boolean;
  catalogWindow: CatalogWindow;
  onStartOver: () => void;
}) {
  const resumed = catalogWindow.resumedFrom !== null;
  return (
    <div className="collection-bar">
      <p aria-live="polite">
        {loading ? (
          "Loading the catalog…"
        ) : items === 0 ? (
          "No matching titles loaded."
        ) : (
          <>
            <strong>{items}</strong> {items === 1 ? "title" : "titles"} loaded
            <span>
              {catalogWindow.hasMore
                ? " · more available"
                : resumed
                  ? " · end of this continuation"
                  : " · end of results"}
            </span>
          </>
        )}
      </p>
      {resumed ? (
        <button className="button-quiet" onClick={onStartOver} type="button">
          Start from the beginning
        </button>
      ) : null}
      {catalogWindow.restartedFromStaleCursor ? (
        <p className="browse-notice" role="status">
          That page link no longer matches these filters, so results start from
          the beginning.
        </p>
      ) : null}
    </div>
  );
}

function CatalogGridSkeleton() {
  return (
    <div aria-live="polite" className="catalog-grid catalog-skeleton" role="status">
      <span className="visually-hidden">Loading catalog results</span>
      {Array.from({ length: 12 }, (_, index) => (
        <div aria-hidden="true" className="catalog-skeleton-cell" key={index}>
          <div className="catalog-skeleton-poster" />
          <div className="skeleton-line skeleton-line-strong" />
          <div className="skeleton-line" />
        </div>
      ))}
    </div>
  );
}

/**
 * Keeps the URL's resume point in step with the window without asking the
 * router to navigate: `history.replaceState` updates the address and the
 * router's view of it, where a `replace` would cost a server round trip on
 * every "load more".
 */
/** The preview endpoint carries its own failure-injection query. */
function withQuery(endpoint: string, params: URLSearchParams): string {
  return `${endpoint}${endpoint.includes("?") ? "&" : "?"}${params.toString()}`;
}

function syncCursorInUrl(
  browsePath: string,
  query: BrowseQuery,
  cursor: string | null,
  persisted?: Record<string, string>,
): void {
  if (typeof window === "undefined") return;
  const href = browseHref(browsePath, { ...query, cursor }, persisted);
  if (href === `${window.location.pathname}${window.location.search}`) return;
  window.history.replaceState(window.history.state, "", href);
}
