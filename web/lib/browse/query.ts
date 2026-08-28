/**
 * Browse query state, and its one canonical spelling in a URL.
 *
 * The URL is the source of truth for what the viewer is looking at, so this
 * module owns the round trip in both directions. Two rules carry most of the
 * weight:
 *
 * 1. There is exactly one spelling of a given query. Defaults and blanks are
 *    omitted and the parameter order is fixed, so the same filter set always
 *    produces the same string. Restoration keys and effect dependencies are
 *    derived from that string, and both break quietly if it can drift.
 * 2. A catalog cursor is bound to the search/filter/sort fingerprint it was
 *    issued for — reusing it against a different query is a `400` from the
 *    endpoint, not a silent skip. So every filter edit drops the cursor here,
 *    at the one place filters can change, rather than at each call site.
 */

/** Offer order, and the order the sort control lists them in. */
export const BROWSE_SORTS = ["popular", "title", "newest"] as const;

export type BrowseSort = (typeof BROWSE_SORTS)[number];

/**
 * Browse opens on what this tenant actually watches.
 *
 * Alphabetical was the original default, which opens a discovery product on
 * `2001`, `Ace Ventura`, `Aladdin` — an ordering that carries no signal and,
 * against the seeded catalog, sorts most of the poster-backed titles off the
 * first page. `popular` is the endpoint's interaction-count ordering, so the
 * first viewport is the part of the catalog the demo can actually show.
 */
export const DEFAULT_BROWSE_SORT: BrowseSort = "popular";

/** The endpoint's default page size; its hard maximum is 48. */
export const CATALOG_PAGE_LIMIT = 24;
export const CATALOG_PAGE_LIMIT_MAX = 48;

/** Mirrors the endpoint's accepted release-year window. */
export const MIN_RELEASE_YEAR = 1878;
export const MAX_RELEASE_YEAR = 2100;

/** The endpoint caps `q` at 120 characters and `genre` at 40. */
const MAX_SEARCH_LENGTH = 120;
const MAX_GENRE_LENGTH = 40;

export type BrowseQuery = {
  q: string;
  genre: string | null;
  yearFrom: number | null;
  yearTo: number | null;
  sort: BrowseSort;
  /** Where the currently loaded window starts, not a page number. */
  cursor: string | null;
};

export const DEFAULT_BROWSE_QUERY: BrowseQuery = {
  q: "",
  genre: null,
  yearFrom: null,
  yearTo: null,
  sort: DEFAULT_BROWSE_SORT,
  cursor: null,
};

/** Accepts both `URLSearchParams` and Next's `ReadonlyURLSearchParams`. */
export type ReadableSearchParams = { get(name: string): string | null };

function normalizeSearch(value: string | null): string {
  if (!value) return "";
  return value.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_SEARCH_LENGTH);
}

function normalizeGenre(value: string | null): string | null {
  const trimmed = value?.trim().slice(0, MAX_GENRE_LENGTH) ?? "";
  return trimmed || null;
}

function normalizeYear(value: string | null): number | null {
  if (!value || !/^\d{1,4}$/.test(value.trim())) return null;
  const year = Number(value.trim());
  return year >= MIN_RELEASE_YEAR && year <= MAX_RELEASE_YEAR ? year : null;
}

function normalizeSort(value: string | null): BrowseSort {
  return BROWSE_SORTS.includes(value as BrowseSort)
    ? (value as BrowseSort)
    : DEFAULT_BROWSE_SORT;
}

function normalizeCursor(value: string | null): string | null {
  const trimmed = value?.trim() ?? "";
  // Opaque to us, but length-bounded by the endpoint; anything longer is not a
  // cursor we issued and would only earn a 400.
  return trimmed && trimmed.length <= 1024 ? trimmed : null;
}

export function parseBrowseQuery(params: ReadableSearchParams): BrowseQuery {
  const yearFrom = normalizeYear(params.get("year_from"));
  const yearTo = normalizeYear(params.get("year_to"));
  // An inverted range is a 422 from the endpoint and is almost always a
  // hand-edited URL. Dropping both bounds shows the unfiltered catalog rather
  // than guessing which bound the viewer meant.
  const inverted = yearFrom !== null && yearTo !== null && yearFrom > yearTo;

  return {
    q: normalizeSearch(params.get("q")),
    genre: normalizeGenre(params.get("genre")),
    yearFrom: inverted ? null : yearFrom,
    yearTo: inverted ? null : yearTo,
    sort: normalizeSort(params.get("sort")),
    cursor: normalizeCursor(params.get("cursor")),
  };
}

/**
 * The canonical URL spelling. Defaults are omitted so a first visit and a
 * cleared-filters visit produce the same address.
 */
export function browseSearchParams(query: BrowseQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.genre) params.set("genre", query.genre);
  if (query.yearFrom !== null) params.set("year_from", String(query.yearFrom));
  if (query.yearTo !== null) params.set("year_to", String(query.yearTo));
  if (query.sort !== DEFAULT_BROWSE_SORT) params.set("sort", query.sort);
  if (query.cursor) params.set("cursor", query.cursor);
  return params;
}

/**
 * Builds a Browse address.
 *
 * `persisted` carries route parameters that are not part of the catalog query
 * but must survive every edit — the selected demo persona above all. Dropping
 * it on a filter click would silently move the viewer to a different persona's
 * catalog state, which is the kind of bug that only shows up as "my watchlist
 * disappeared".
 */
export function browseHref(
  path: string,
  query: BrowseQuery,
  persisted?: Record<string, string>,
): string {
  const params = new URLSearchParams(persisted);
  for (const [name, value] of browseSearchParams(query)) params.set(name, value);
  return params.size ? `${path}?${params.toString()}` : path;
}

/**
 * Identity of a *filter set*, deliberately excluding the cursor.
 *
 * Everything that must be reloaded when the viewer changes what they are
 * looking for keys off this; paging deeper into the same query must not.
 */
export function browseFilterKey(query: BrowseQuery): string {
  return browseSearchParams({ ...query, cursor: null }).toString();
}

/**
 * Applies a filter edit. The cursor is always dropped: it belongs to the query
 * fingerprint it was issued under, and carrying it into a new filter set only
 * earns a 400.
 */
export function withBrowseFilters(
  query: BrowseQuery,
  patch: Partial<Omit<BrowseQuery, "cursor">>,
): BrowseQuery {
  const next: BrowseQuery = { ...query, ...patch, cursor: null };
  const inverted =
    next.yearFrom !== null && next.yearTo !== null && next.yearFrom > next.yearTo;
  return inverted ? { ...next, yearFrom: null, yearTo: null } : next;
}

/**
 * Whether the viewer has narrowed the catalog.
 *
 * The sort is deliberately excluded. It is a reordering of the same result
 * set, not a filter, and it has its own always-visible control — so counting
 * it here would light up an "Active filters" row that has nothing to show,
 * and would offer to "remove" an ordering that cannot be absent.
 */
export function hasActiveBrowseFilters(query: BrowseQuery): boolean {
  return browseFilterKey({ ...query, sort: DEFAULT_BROWSE_SORT }).length > 0;
}

/** Query string for `GET /users/{id}/catalog` through the BFF. */
export function catalogRequestParams(
  query: BrowseQuery,
  options: { cursor?: string | null; limit?: number } = {},
): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.genre) params.set("genre", query.genre);
  if (query.yearFrom !== null) params.set("year_from", String(query.yearFrom));
  if (query.yearTo !== null) params.set("year_to", String(query.yearTo));
  params.set("sort", query.sort);
  params.set(
    "limit",
    String(
      Math.min(
        Math.max(1, Math.trunc(options.limit ?? CATALOG_PAGE_LIMIT)),
        CATALOG_PAGE_LIMIT_MAX,
      ),
    ),
  );
  const cursor = normalizeCursor(options.cursor ?? null);
  if (cursor) params.set("cursor", cursor);
  return params;
}
