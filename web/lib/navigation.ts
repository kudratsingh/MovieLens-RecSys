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

/**
 * Where a movie may send a viewer back to.
 *
 * A movie is reachable from three collections and every one of them keeps state
 * worth returning to — a Browse window and its scroll position, a Library tab
 * and its filter, a Discover ranking. Detail used to honour only Browse, so a
 * viewer who opened a title from Library was quietly redirected into the
 * catalog on the way out. The allow-list is narrow on purpose: `returnTo`
 * arrives from a link the browser controls, so anything not named here is
 * discarded rather than followed.
 */
export const RETURNABLE_ROUTES = ["/browse", "/library", "/discover"] as const;

const RETURN_LABELS: Record<string, string> = {
  "/browse": "Back to Browse",
  "/library": "Back to Library",
  "/discover": "Back to For you",
};

function returnRoute(href: string): string | null {
  // The recorded preview mirrors the live routes under its own prefix.
  const path = href.startsWith("/ui-preview/") ? href.slice("/ui-preview".length) : href;
  return (
    RETURNABLE_ROUTES.find(
      (route) => path === route || path.startsWith(`${route}?`),
    ) ?? null
  );
}

export function safeReturnHref(
  value: string | string[] | undefined,
  fallback: string,
): string {
  const first = Array.isArray(value) ? value[0] : value;
  if (typeof first !== "string") return fallback;
  // A protocol-relative or backslash-prefixed value would leave the origin.
  if (first.includes("//") || first.includes("\\")) return fallback;
  return returnRoute(first) ? first : fallback;
}

/** The label for a back link, so the destination is named rather than guessed. */
export function returnHrefLabel(href: string): string {
  const route = returnRoute(href);
  return route ? RETURN_LABELS[route] : "Back";
}
