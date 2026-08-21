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
 */
const PERSONALIZED_ROUTES = ["/", "/discover", "/legacy"];

export function middleware(request: NextRequest) {
  const response = NextResponse.next();
  if (PERSONALIZED_ROUTES.includes(request.nextUrl.pathname)) {
    response.headers.set("Cache-Control", "private, no-store");
  }
  return response;
}

export const config = { matcher: ["/", "/discover", "/legacy"] };
