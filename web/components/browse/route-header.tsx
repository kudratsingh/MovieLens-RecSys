/**
 * The route-owned header for Browse and movie detail.
 *
 * Bundle 4's shell belongs to the recorded preview and points at its routes,
 * so the live catalog slice carries its own until the shell is promoted onto
 * the authenticated routes. It keeps the distinction the product depends on
 * visible: the signed-in actor and the demo persona whose state is on screen
 * are labelled separately, because these routes are role-gated persona mode
 * and not somebody's private library.
 */

import Link from "next/link";

import { SignOutButton } from "@/components/auth-controls";
import "./route-header.css";

export function CatalogRouteHeader({
  userId,
  actorName,
  current,
}: {
  userId: number;
  actorName: string;
  current: "browse" | "movie";
}) {
  return (
    <header className="catalog-header">
      <Link className="catalog-wordmark" href="/">
        <span aria-hidden="true">ML</span>
        <span>
          <strong>MovieLens</strong>
          <small>Two-stage recommender</small>
        </span>
      </Link>

      <nav aria-label="Primary" className="catalog-nav">
        <Link href="/">Discover</Link>
        <Link
          aria-current={current === "browse" ? "page" : undefined}
          href={`/browse?user=${userId}`}
        >
          Browse
        </Link>
        <Link href={`/library?userId=${userId}`}>Library</Link>
      </nav>

      <div className="catalog-session">
        <span>
          <small>Signed in as</small>
          <strong>{actorName}</strong>
        </span>
        <span>
          <small>Exploring as persona</small>
          <strong>{userId}</strong>
        </span>
        <SignOutButton />
      </div>
    </header>
  );
}
