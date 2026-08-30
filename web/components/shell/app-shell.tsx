import Link from "next/link";

import { SignOutButton } from "@/components/auth-controls";
import { ProductNavigation } from "@/components/shell/product-navigation";
import { TmdbAttribution } from "@/components/ui/tmdb-attribution";
import type { NavigationItem } from "@/lib/navigation";

/**
 * The authenticated product shell.
 *
 * The persona block is not decoration. A selected MovieLens persona is not the
 * signed-in human, and the shell has to keep those two identities visibly
 * apart until `(tenant, subject)` maps to an owned profile. Defaults preserve
 * the Bundle 4 recorded preview wording; live routes pass their own.
 *
 * Every authenticated route renders this shell. Browse and movie detail used
 * to run a header of their own, which dropped the bottom navigation the design
 * contract requires on small screens and printed the persona as a raw numeric
 * ID; retiring it is what makes the mobile navigation and the resolved persona
 * name properties of the product rather than of two routes out of five.
 *
 * Both identity lines are always rendered. They used to be `display: none`
 * below 1050px, which took the labelled spans out of the accessibility tree as
 * well as off the screen and left an `aria-hidden` two-letter dot as the only
 * answer to "whose data is this" on a phone — on Browse and movie detail, the
 * two routes that restate the persona nowhere else. The layout moves at
 * `shell.css`; the markup does not, because there is no width at which the
 * contract's "authenticated actor plus an explicitly labeled selected demo
 * persona" stops applying.
 */
export function AppShell({
  actorName,
  children,
  fixtureMode,
  homeHref = "/ui-preview/discover",
  homeLabel = "MovieLens UI preview — For you",
  wordmarkSubtitle = "Recorded UI preview",
  personaLabel = "Recorded persona",
  personaName = "Action Fan",
  personaInitials,
  navigationItems,
  legacyHref,
}: {
  actorName: string;
  children: React.ReactNode;
  fixtureMode: boolean;
  homeHref?: string;
  homeLabel?: string;
  wordmarkSubtitle?: string;
  personaLabel?: string;
  personaName?: string;
  personaInitials?: string;
  navigationItems?: readonly NavigationItem[];
  /**
   * The pre-redesign dashboard, when the route wants to offer it.
   *
   * Deliberately a utility link at the foot of the page and never a
   * navigation slot: it is a retained rollback, not a fourth destination.
   */
  legacyHref?: string;
}) {
  const initials =
    personaInitials ??
    personaName
      .split(" ")
      .slice(0, 2)
      .map((word) => word[0] ?? "")
      .join("")
      .toUpperCase();

  return (
    <div className="min-h-dvh">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="shell-header">
        <Link aria-label={homeLabel} className="wordmark" href={homeHref}>
          <span aria-hidden="true" className="wordmark-mark">
            ML
          </span>
          <span className="wordmark-copy">
            <strong>MovieLens</strong>
            <small>{wordmarkSubtitle}</small>
          </span>
        </Link>

        <ProductNavigation items={navigationItems} location="desktop" />

        <div className="shell-session">
          {/* The initials repeat the persona name beside them, so they are
              decoration for a reader rather than a second identity claim. */}
          <span aria-hidden="true" className="persona-dot">
            {initials}
          </span>
          <span className="actor-copy">
            <small>{fixtureMode ? "Isolated mode" : "Signed in as"}</small>
            <strong>{actorName}</strong>
          </span>
          {/* `persona-cluster` also anchors the service-backed finish-gate
              journey's persona assertion, so the name stays even though the
              dot moved out of it. */}
          <span className="persona-cluster">
            <small>{personaLabel}</small>
            <strong>{personaName}</strong>
          </span>
          <span className="shell-session-action">
            {fixtureMode ? (
              <Link className="shell-exit" href="/">
                Exit preview
              </Link>
            ) : (
              <SignOutButton />
            )}
          </span>
        </div>
      </header>

      <main id="main-content">{children}</main>

      {/*
        The footer is unconditional now, because the TMDB notice is. It used to
        appear only where a `legacyHref` was passed, which meant Discover in
        fixture mode and every `/ui-preview` route rendered no footer at all —
        and the attribution TMDB's terms require lived nowhere in the product,
        only on the pre-redesign dashboard that is due to be retired.
      */}
      <footer className="shell-footer">
        {legacyHref ? (
          <p className="shell-footer-legacy">
            <Link href={legacyHref}>Legacy dashboard</Link>
            <span>
              The pre-redesign surface, kept as the rollback for this cutover.
            </span>
          </p>
        ) : null}
        <TmdbAttribution />
      </footer>

      <ProductNavigation items={navigationItems} location="mobile" />
    </div>
  );
}
