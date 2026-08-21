/**
 * Primary navigation sets.
 *
 * These live outside the navigation component because a server-rendered route
 * shell has to build its own set, and a `"use client"` module cannot export a
 * function the server may call.
 */

export type NavigationItem = {
  href: string;
  label: string;
  icon: "spark" | "compass" | "library";
  /** Path prefix used for the active check when `href` carries a query. */
  match?: string;
};

export const previewNavigationItems: readonly NavigationItem[] = [
  { href: "/ui-preview/discover", label: "For you", icon: "spark" },
  { href: "/ui-preview/browse", label: "Browse", icon: "compass" },
  { href: "/ui-preview/library", label: "Library", icon: "library" },
];

/**
 * The live routes carry the selected persona in the query string, and each of
 * them reads it under a different name today. Building the set from one place
 * keeps that inconsistency in a single spot until the routes converge.
 */
export function productNavigationItems(userId: number): readonly NavigationItem[] {
  return [
    { href: `/discover?userId=${userId}`, label: "For you", icon: "spark", match: "/discover" },
    { href: `/browse?user=${userId}`, label: "Browse", icon: "compass", match: "/browse" },
    { href: `/library?userId=${userId}`, label: "Library", icon: "library", match: "/library" },
  ];
}
