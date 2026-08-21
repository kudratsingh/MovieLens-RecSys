import Link from "next/link";

import { SignOutButton } from "@/components/auth-controls";
import { ProductNavigation } from "@/components/shell/product-navigation";

export function AppShell({
  actorName,
  children,
  fixtureMode,
}: {
  actorName: string;
  children: React.ReactNode;
  fixtureMode: boolean;
}) {
  return (
    <div className="min-h-dvh">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="shell-header">
        <Link aria-label="MovieLens UI preview — For you" className="wordmark" href="/ui-preview/discover">
          <span aria-hidden="true" className="wordmark-mark">
            ML
          </span>
          <span className="wordmark-copy">
            <strong>MovieLens</strong>
            <small>Recorded UI preview</small>
          </span>
        </Link>

        <ProductNavigation location="desktop" />

        <div className="shell-session">
          <span className="actor-copy">
            <small>{fixtureMode ? "Isolated mode" : "Signed in as"}</small>
            <strong>{actorName}</strong>
          </span>
          <span className="persona-cluster">
            <span className="persona-dot" aria-hidden="true">AF</span>
            <span>
              <small>Recorded persona</small>
              <strong>Action Fan</strong>
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
      <ProductNavigation location="mobile" />
    </div>
  );
}
