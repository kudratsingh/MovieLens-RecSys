/**
 * The genre and decade facets Browse offers.
 *
 * These are a curated subset of the MovieLens genre vocabulary rather than a
 * live facet count. The endpoint does not return facet counts, and inventing
 * them would be exactly the sort of made-up total the catalog contract avoids
 * — a filter chip that promises "Drama (37)" is a claim the API never made.
 * Any genre still works through a deep link; the chips are the common ones.
 */

export const BROWSE_GENRES = [
  "Action",
  "Animation",
  "Comedy",
  "Crime",
  "Drama",
  "Mystery",
  "Romance",
  "Sci-Fi",
  "Thriller",
] as const;

export type BrowseDecade = {
  label: string;
  yearFrom: number;
  yearTo: number;
};

export const BROWSE_DECADES: readonly BrowseDecade[] = [
  { label: "Before 1980", yearFrom: 1878, yearTo: 1979 },
  { label: "1980s", yearFrom: 1980, yearTo: 1989 },
  { label: "1990s", yearFrom: 1990, yearTo: 1999 },
  { label: "2000s", yearFrom: 2000, yearTo: 2009 },
  { label: "2010s", yearFrom: 2010, yearTo: 2019 },
  { label: "2020s", yearFrom: 2020, yearTo: 2029 },
];

export const BROWSE_SORT_LABELS = {
  title: "Title A–Z",
  newest: "Newest first",
  popular: "Most watched here",
} as const;
