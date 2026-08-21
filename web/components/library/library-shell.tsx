import Link from "next/link";

import { SignOutButton } from "@/components/auth-controls";
import { Icon } from "@/components/ui/icons";
import { titleInitials } from "@/lib/library/collection";
import "@/components/shell/shell.css";

const NAVIGATION = [
  { href: "/", label: "For you", icon: "spark" as const },
  { href: "/browse", label: "Browse", icon: "compass" as const },
  { href: "/library", label: "Library", icon: "library" as const },
];

/**
 * The route-owned shell for the live Library.
 *
 * Bundle 4's `AppShell` is wired to the recorded `/ui-preview` routes and to a
 * hard-coded recorded persona, so the live route keeps its own header while
 * reusing the shell's visual language. Converging the two belongs with the
 * cutover that retires the preview routes, not with a single route slice.
 */
export function LibraryShell({
  actorName,
  personaLabel,
  children,
}: {
  actorName: string;
  personaLabel: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-dvh">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>

      <header className="shell-header">
        <Link aria-label="MovieLens — For you" className="wordmark" href="/">
          <span aria-hidden="true" className="wordmark-mark">
            ML
          </span>
          <span className="wordmark-copy">
            <strong>MovieLens</strong>
            <small>Two-stage recommender</small>
          </span>
        </Link>

        <nav aria-label="Primary" className="top-navigation">
          {NAVIGATION.map((item) => (
            <Link
              aria-current={item.href === "/library" ? "page" : undefined}
              href={item.href}
              key={item.href}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </Link>
          ))}
        </nav>

        <div className="shell-session">
          <span className="actor-copy">
            <small>Signed in as</small>
            <strong>{actorName}</strong>
          </span>
          <span className="persona-cluster">
            <span aria-hidden="true" className="persona-dot">
              {titleInitials(personaLabel)}
            </span>
            <span>
              <small>Selected persona</small>
              <strong>{personaLabel}</strong>
            </span>
          </span>
          <SignOutButton />
        </div>
      </header>

      <main id="main-content">{children}</main>

      <nav aria-label="Primary mobile" className="bottom-navigation">
        {NAVIGATION.map((item) => (
          <Link
            aria-current={item.href === "/library" ? "page" : undefined}
            href={item.href}
            key={item.href}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </Link>
        ))}
      </nav>
    </div>
  );
}
