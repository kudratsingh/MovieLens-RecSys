/**
 * The Library route's URL is the single owner of tab, sort, filter, and page
 * position.
 *
 * Keeping the round trip here — rather than inside the client component — is
 * what makes it testable and what keeps a shared or reloaded link showing the
 * same collection. Two rules are worth stating because they are easy to get
 * wrong:
 *
 * 1. Only non-default values are written, so `/library?userId=900000101` is the
 *    canonical resting URL rather than a query string full of defaults.
 * 2. A cursor is bound by the API to its tab, sort, and *every* filter, so
 *    changing any of those drops it. Carrying a stale cursor across a view
 *    change is exactly the request the backend rejects.
 *
 * The genre and year filters are parsed on every tab so a deep link is
 * honoured wherever it points; which tab *offers* them is a presentation
 * decision and lives with the controls.
 */

export const LIBRARY_TABS = ["rated", "watchlist", "history"] as const;
export type LibraryTab = (typeof LIBRARY_TABS)[number];

export const LIBRARY_SORTS = [
  "recent",
  "title",
  "rating",
  "release",
  "tmdb",
] as const;
export type LibrarySort = (typeof LIBRARY_SORTS)[number];

export const DEFAULT_LIBRARY_TAB: LibraryTab = "rated";
export const DEFAULT_LIBRARY_SORT: LibrarySort = "recent";

/** Matches the demo persona the rest of the product defaults to. */
export const DEFAULT_LIBRARY_USER_ID = 900000101;

/** Well inside the endpoint's cap of 50, and enough to fill a desktop panel. */
export const LIBRARY_PAGE_SIZE = 12;

/** The endpoint's own bounds, so a hand-edited URL is corrected here. */
export const MIN_RELEASE_YEAR = 1878;
export const MAX_RELEASE_YEAR = 2100;
const MAX_SEARCH_LENGTH = 120;
const MAX_GENRE_LENGTH = 40;
const MAX_CURSOR_LENGTH = 1024;

export type LibraryUrlState = {
  userId: number;
  tab: LibraryTab;
  sort: LibrarySort;
  query: string;
  genre: string | null;
  yearFrom: number | null;
  yearTo: number | null;
  /** Opaque continuation token for the active tab, or null for its first page. */
  cursor: string | null;
};

/** What a cursor is bound to. Editing any of these invalidates it. */
export type LibraryFilters = Pick<
  LibraryUrlState,
  "tab" | "sort" | "query" | "genre" | "yearFrom" | "yearTo"
>;

export type LibrarySearchParams = Record<string, string | string[] | undefined>;

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export function isLibraryTab(value: unknown): value is LibraryTab {
  return LIBRARY_TABS.includes(value as LibraryTab);
}

/**
 * Which orderings each collection offers.
 *
 * `rating` only means something where every row carries one, so Watchlist does
 * not offer it — a watchlisted title cannot have a star value, and the endpoint
 * refuses the combination. `release` and `tmdb` order movie facts every tab's
 * rows have and the endpoint accepts them everywhere; they are offered on Seen
 * alone for now, which is a UI decision and one line to revisit.
 */
export function sortsForTab(tab: LibraryTab): readonly LibrarySort[] {
  if (tab === "watchlist") return ["recent", "title"] as const;
  if (tab === "rated") return ["recent", "title", "rating"] as const;
  return LIBRARY_SORTS;
}

export function normalizeSort(tab: LibraryTab, sort: unknown): LibrarySort {
  const allowed = sortsForTab(tab);
  return allowed.includes(sort as LibrarySort)
    ? (sort as LibrarySort)
    : DEFAULT_LIBRARY_SORT;
}

function parseUserId(value: string | undefined): number {
  if (!value || !/^\d+$/.test(value)) return DEFAULT_LIBRARY_USER_ID;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0
    ? parsed
    : DEFAULT_LIBRARY_USER_ID;
}

/**
 * Collapses whitespace runs the way the endpoint does before it fingerprints
 * the query. `"  the   thing "` and `"the thing"` have to be one view, or the
 * same search typed two ways would issue two incompatible cursors.
 */
function normalizeQuery(value: string | undefined | null): string {
  if (!value) return "";
  return value.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_SEARCH_LENGTH);
}

function normalizeGenre(value: string | undefined | null): string | null {
  const trimmed = value?.trim().slice(0, MAX_GENRE_LENGTH) ?? "";
  return trimmed || null;
}

/**
 * A release-year bound, from a URL parameter or from a form field.
 *
 * Exported because the year controls type into the same shape the URL carries,
 * and a half-typed "19" or a pasted "not a year" has to land as "no bound"
 * rather than as a `NaN` written into the address bar.
 */
export function parseLibraryYear(value: string | undefined | null): number | null {
  if (!value || !/^\d{1,4}$/.test(value.trim())) return null;
  const year = Number(value.trim());
  return year >= MIN_RELEASE_YEAR && year <= MAX_RELEASE_YEAR ? year : null;
}

function clampYear(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const year = Math.trunc(value);
  return year >= MIN_RELEASE_YEAR && year <= MAX_RELEASE_YEAR ? year : null;
}

/**
 * An inverted range drops both bounds rather than guessing which one the
 * viewer meant — the endpoint answers `year_from > year_to` with a 422, and
 * `parseBrowseQuery` already made the same call for the same reason.
 */
function withValidYearRange<T extends { yearFrom: number | null; yearTo: number | null }>(
  state: T,
): T {
  const inverted =
    state.yearFrom !== null && state.yearTo !== null && state.yearFrom > state.yearTo;
  return inverted ? { ...state, yearFrom: null, yearTo: null } : state;
}

export function parseLibraryUrlState(
  params: LibrarySearchParams,
): LibraryUrlState {
  const requestedTab = single(params.tab);
  const tab = isLibraryTab(requestedTab) ? requestedTab : DEFAULT_LIBRARY_TAB;
  const cursor = single(params.cursor)?.trim();
  return withValidYearRange({
    userId: parseUserId(single(params.userId)),
    tab,
    sort: normalizeSort(tab, single(params.sort)),
    query: normalizeQuery(single(params.q)),
    genre: normalizeGenre(single(params.genre)),
    yearFrom: parseLibraryYear(single(params.year_from)),
    yearTo: parseLibraryYear(single(params.year_to)),
    cursor: cursor && cursor.length <= MAX_CURSOR_LENGTH ? cursor : null,
  });
}

export const LIBRARY_BASE_PATH = "/library";

export function libraryUrlQuery(
  state: LibraryUrlState,
  extra: Record<string, string> = {},
): URLSearchParams {
  const params = new URLSearchParams({ userId: String(state.userId) });
  if (state.tab !== DEFAULT_LIBRARY_TAB) params.set("tab", state.tab);
  if (state.sort !== DEFAULT_LIBRARY_SORT) params.set("sort", state.sort);
  if (state.query) params.set("q", state.query);
  if (state.genre) params.set("genre", state.genre);
  if (state.yearFrom !== null) params.set("year_from", String(state.yearFrom));
  if (state.yearTo !== null) params.set("year_to", String(state.yearTo));
  if (state.cursor) params.set("cursor", state.cursor);
  // Preview-only knobs (`fail`, `empty`) ride along so a recorded state
  // survives a tab change and a reload.
  for (const [name, value] of Object.entries(extra)) {
    if (value) params.set(name, value);
  }
  return params;
}

export function libraryHref(
  state: LibraryUrlState,
  basePath: string = LIBRARY_BASE_PATH,
  extra: Record<string, string> = {},
): string {
  return `${basePath}?${libraryUrlQuery(state, extra)}`;
}

/** Whether the viewer has narrowed the collection they are looking at. */
export function hasLibraryFilters(state: LibraryUrlState): boolean {
  return Boolean(state.query || state.genre || state.yearFrom || state.yearTo);
}

/**
 * Produces the next URL state for a view change, dropping the cursor whenever
 * the change invalidates it.
 */
export function nextLibraryUrlState(
  current: LibraryUrlState,
  change: Partial<Omit<LibraryUrlState, "userId">>,
): LibraryUrlState {
  const tab = change.tab ?? current.tab;
  const sort = normalizeSort(tab, change.sort ?? current.sort);
  const next = withValidYearRange({
    tab,
    sort,
    query: normalizeQuery(change.query ?? current.query),
    // `"genre" in change` for the same reason the cursor uses it below: an
    // explicit `null` is how "All genres" clears the filter, and a nullish
    // fallback would read that as "no opinion" and keep the old one.
    genre: "genre" in change ? normalizeGenre(change.genre) : current.genre,
    yearFrom: "yearFrom" in change ? clampYear(change.yearFrom) : current.yearFrom,
    yearTo: "yearTo" in change ? clampYear(change.yearTo) : current.yearTo,
  });
  const viewChanged =
    next.tab !== current.tab ||
    next.sort !== current.sort ||
    next.query !== current.query ||
    next.genre !== current.genre ||
    next.yearFrom !== current.yearFrom ||
    next.yearTo !== current.yearTo;
  // `"cursor" in change` rather than `??`: an explicit `{ cursor: null }` is
  // how "Back to the first page" asks for the top of an unchanged view, and a
  // nullish fallback read that as "no opinion" and handed the old cursor back.
  const cursor = "cursor" in change ? (change.cursor ?? null) : current.cursor;
  return { userId: current.userId, ...next, cursor: viewChanged ? null : cursor };
}

/**
 * Identifies the exact request a loaded page came from, so the component can
 * tell "already loaded" from "the view moved and needs a fresh first page".
 *
 * Serialized rather than joined on a separator. Two of these fields are free
 * text a viewer controls, so any separator that can appear inside one lets two
 * different views share a key: `q="The"` with `genre="(no genres listed)"`
 * joined on a space is indistinguishable from `q="The (no"` with
 * `genre="genres listed)"`. That is not hypothetical — `BROWSE_GENRES` is a
 * curated subset and the endpoint takes any genre through a deep link. A
 * collision here is silent and reads as data corruption: the key says nothing
 * moved, so the fetch is skipped and the previous query's rows stay on screen
 * under the new query's URL.
 */
export function libraryViewKey(state: LibraryUrlState): string {
  return JSON.stringify([
    state.userId,
    state.tab,
    state.sort,
    state.query,
    state.genre,
    state.yearFrom,
    state.yearTo,
    state.cursor,
  ]);
}
