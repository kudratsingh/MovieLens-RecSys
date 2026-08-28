import { resolveDemoPersonaId } from "@/lib/demo-persona";

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

/**
 * Where the sign-in door may send a viewer once they have authenticated.
 *
 * A second list rather than a widening of `RETURNABLE_ROUTES`, because the two
 * answer different questions. `RETURNABLE_ROUTES` is "where may a movie's
 * *back* link point", and every entry there owes a phrase in `RETURN_LABELS`;
 * a detail page is not a sensible back target for another detail page. The
 * door asks "is this one of our own product addresses", and the honest answer
 * includes movie detail, Quick Picks, and the retained legacy dashboard — all
 * three of which bounce a signed-out visitor here and used to drop the address
 * they came for on the way.
 */
export const SIGN_IN_RETURN_ROUTES = [
  "/discover",
  "/browse",
  "/library",
  "/movies",
  "/quick-picks",
  "/legacy",
] as const;

const RETURN_LABELS: Record<string, string> = {
  "/browse": "Back to Browse",
  "/library": "Back to Library",
  "/discover": "Back to For you",
};

/** How the door names the place signing in will hand the viewer back to. */
const SIGN_IN_DESTINATIONS: Record<string, string> = {
  "/discover": "For you",
  "/browse": "Browse",
  "/library": "Library",
  "/movies": "that movie",
  "/quick-picks": "Quick picks",
  "/legacy": "the legacy dashboard",
};

/**
 * A return address is only ever a path on this origin, and every rejection
 * below is a way out of the app: a protocol-relative `//host`, a backslash some
 * browsers normalize into a slash, an absolute URL, a control character that
 * would smuggle a second line into a log or a header, or a value long enough
 * that it is not one of our addresses at all.
 */
const MAX_RETURN_LENGTH = 512;

function withinOrigin(value: string): boolean {
  return (
    value.startsWith("/") &&
    !value.includes("//") &&
    !value.includes("\\") &&
    value.length <= MAX_RETURN_LENGTH &&
    !/[\u0000-\u001f\u007f]/.test(value)
  );
}

function matchedRoute(href: string, routes: readonly string[]): string | null {
  // The recorded preview mirrors the live routes under its own prefix.
  const path = href.startsWith("/ui-preview/") ? href.slice("/ui-preview".length) : href;
  return (
    routes.find(
      (route) =>
        path === route || path.startsWith(`${route}?`) || path.startsWith(`${route}/`),
    ) ?? null
  );
}

export function safeReturnHref(
  value: string | string[] | undefined,
  fallback: string,
): string {
  const first = Array.isArray(value) ? value[0] : value;
  if (typeof first !== "string" || !withinOrigin(first)) return fallback;
  return matchedRoute(first, RETURNABLE_ROUTES) ? first : fallback;
}

/** The label for a back link, so the destination is named rather than guessed. */
export function returnHrefLabel(href: string): string {
  const route = matchedRoute(href, RETURNABLE_ROUTES);
  return route ? RETURN_LABELS[route] : "Back";
}

/**
 * The address a signed-out visitor asked for, if we are willing to return to it.
 *
 * `null` rather than a fallback, because the two callers want different things
 * out of a rejection: a protected route falls back to a bare `/`, and the door
 * itself falls back to the persona-carrying product address.
 */
export function safeSignInReturn(
  value: string | string[] | null | undefined,
): string | null {
  const first = Array.isArray(value) ? value[0] : value;
  if (typeof first !== "string" || !withinOrigin(first)) return null;
  return matchedRoute(first, SIGN_IN_RETURN_ROUTES) ? first : null;
}

/**
 * Names the destination for the door's copy, so a viewer who was bounced out of
 * a deep link is told where signing in will put them back.
 */
export function signInDestination(href: string): string | null {
  const route = matchedRoute(href, SIGN_IN_RETURN_ROUTES);
  return route ? SIGN_IN_DESTINATIONS[route] : null;
}

/**
 * The door, carrying the address the viewer actually asked for.
 *
 * Protected routes call this instead of `redirect("/")`. Every signed-out deep
 * link used to land on the default persona's Discover page, so a shared link to
 * a movie — or to another persona's Library tab — was lost at the door, which
 * on a demo whose sessions expire is most of the time a link is followed.
 */
export function signInHref(destination: string): string {
  const target = safeSignInReturn(destination);
  return target ? `/?next=${encodeURIComponent(target)}` : "/";
}

/**
 * Rebuilds the address a route was asked for out of its own search parameters.
 *
 * A server component cannot read its own URL, and hand-listing the parameters
 * each route cares about is how a Library tab or a Browse cursor goes missing
 * later. Values are re-encoded through `URLSearchParams`, so a separator a
 * caller tried to smuggle in arrives as text — and `safeSignInReturn` still has
 * the final say over the result.
 */
export function routeReturnHref(
  pathname: string,
  params: Record<string, string | string[] | undefined> = {},
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const first = Array.isArray(value) ? value[0] : value;
    if (typeof first === "string" && first !== "") query.set(key, first);
  }
  const search = query.toString();
  return search ? `${pathname}?${search}` : pathname;
}

/**
 * Where a signed-in viewer is sent when they arrive at `/`.
 *
 * The front door redirects rather than rendering Discover a second time under
 * a different address. Three things pay for that choice: the primary
 * navigation decides which slot is current from the pathname, so a second URL
 * serving the same route would leave `For you` unmarked; the personalized
 * `Cache-Control` header stays attached to one path instead of two; and
 * `/discover?userId=` remains the only address that carries a persona, which
 * is what makes a link shareable between the shell, the movie detail
 * `returnTo`, and the evidence scripts.
 *
 * A validated `next` outranks all of that. It is the address the viewer asked
 * for before the door interrupted them, and it carries its own persona.
 *
 * Both spellings of the persona parameter are accepted because the live routes
 * still disagree about the name — Discover reads `userId`, Browse and detail
 * read `user` — so a link either of them produced keeps its persona across the
 * door instead of silently landing on the default one.
 */
export function frontDoorHref(params: {
  user?: string | string[];
  userId?: string | string[];
  next?: string | string[];
}): string {
  const requested = safeSignInReturn(params.next);
  if (requested) return requested;
  return `/discover?userId=${resolveDemoPersonaId(params.userId ?? params.user)}`;
}
