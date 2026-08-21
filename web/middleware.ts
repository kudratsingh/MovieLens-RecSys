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
 */
const PERSONALIZED_ROUTES = ["/discover"];

export function middleware(request: NextRequest) {
  const response = NextResponse.next();
  if (PERSONALIZED_ROUTES.includes(request.nextUrl.pathname)) {
    response.headers.set("Cache-Control", "private, no-store");
  }
  return response;
}

export const config = { matcher: ["/discover"] };
