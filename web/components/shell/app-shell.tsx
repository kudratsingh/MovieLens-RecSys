import Link from "next/link";

import { SignOutButton } from "@/components/auth-controls";
import { ProductNavigation } from "@/components/shell/product-navigation";
import type { NavigationItem } from "@/lib/navigation";

/**
 * The authenticated product shell.
 *
 * The persona block is not decoration. A selected MovieLens persona is not the
 * signed-in human, and the shell has to keep those two identities visibly
 * apart until `(tenant, subject)` maps to an owned profile. Defaults preserve
 * the Bundle 4 recorded preview wording; live routes pass their own.
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
          <span className="actor-copy">
            <small>{fixtureMode ? "Isolated mode" : "Signed in as"}</small>
            <strong>{actorName}</strong>
          </span>
          <span className="persona-cluster">
            <span className="persona-dot" aria-hidden="true">{initials}</span>
            <span>
              <small>{personaLabel}</small>
              <strong>{personaName}</strong>
            </span>
          </span>
          {fixtureMode ? (
            <Link className="shell-exit" href="/">Exit preview</Link>
          ) : (
            <SignOutButton />
          )}
        </div>
      </header>

      <main id="main-content">{children}</main>
      <ProductNavigation items={navigationItems} location="mobile" />
    </div>
  );
}
