import { NextResponse, type NextRequest } from "next/server";

/**
 * Personalized documents are marked uncacheable at the edge.
 *
 * The BFF already returns `private, no-store` for the JSON it serves, but a
 * route like `/discover` renders one persona's state, under one tenant, into
 * the HTML itself. Next's own default for a dynamic page (`no-cache,
 * must-revalidate`) still permits a shared cache to store it, and `private` is
 * the part that matters when a CDN sits in front of the app. Setting it here
 * rather than in `next.config.ts` is deliberate: the framework overwrites the
 * configured header for dynamically rendered pages.
 *
 * `/` joined the list with the cutover. It no longer renders one document for
 * everyone: signed out it is the sign-in door, and signed in it is a redirect
 * carrying the viewer's persona. Either answer cached in a shared cache would
 * be served to the wrong visitor. `/legacy` renders a persona's dashboard for
 * the same reason.
 *
 * `/library`, `/movies/*` and `/quick-picks` were left off the original list
 * even though they meet the same criterion — each one renders a named
 * persona's saved state, and detail and Quick Picks both render that persona's
 * feedback controls in their committed positions. The rule is the document's
 * content, not how personalized the route feels.
 */
const PERSONALIZED_ROUTES = [
  "/",
  "/discover",
  "/legacy",
  "/library",
  "/quick-picks",
];

/** Movie detail is `/movies/{id}`, so it is matched by prefix rather than exactly. */
const PERSONALIZED_PREFIXES = ["/movies/"];

export function isPersonalizedDocument(pathname: string): boolean {
  return (
    PERSONALIZED_ROUTES.includes(pathname) ||
    PERSONALIZED_PREFIXES.some((prefix) => pathname.startsWith(prefix))
  );
}

export function middleware(request: NextRequest) {
  const response = NextResponse.next();
  if (isPersonalizedDocument(request.nextUrl.pathname)) {
    response.headers.set("Cache-Control", "private, no-store");
  }
  return response;
}

export const config = {
  matcher: [
    "/",
    "/discover",
    "/legacy",
    "/library",
    "/quick-picks",
    "/movies/:path*",
  ],
};
