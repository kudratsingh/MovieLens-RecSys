import { render, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/components/shell/app-shell";
import { productNavigationItems } from "@/lib/navigation";

const PERSONA = 900000103;

function renderShell(overrides: Partial<Parameters<typeof AppShell>[0]> = {}) {
  return render(
    <AppShell
      actorName="demo"
      fixtureMode={false}
      homeHref={`/discover?userId=${PERSONA}`}
      homeLabel="MovieLens — For you"
      legacyHref="/legacy"
      navigationItems={productNavigationItems(PERSONA)}
      personaLabel="Exploring as"
      personaName="Eclectic Viewer"
      wordmarkSubtitle="Recommendation lab"
      {...overrides}
    >
      <div className="app-page">
        <h1>Every good detour starts with a title.</h1>
      </div>
    </AppShell>,
  );
}

/**
 * The shell every authenticated route renders after the cutover.
 *
 * Browse and movie detail used to run a header of their own that dropped the
 * mobile navigation the design contract requires and printed the persona as a
 * raw numeric ID. Both were blocking items in the finish-gate review, and both
 * are properties of this component now — so they are asserted here rather than
 * only in a browser at one width.
 */
describe("the authenticated product shell", () => {
  it("offers the three primary routes in both navigations", async () => {
    const { container } = renderShell();

    const desktop = screen.getByRole("navigation", { name: "Primary" });
    const mobile = screen.getByRole("navigation", { name: "Primary mobile" });

    for (const nav of [desktop, mobile]) {
      const links = within(nav).getAllByRole("link");
      expect(links.map((link) => link.textContent)).toEqual([
        "For you",
        "Browse",
        "Library",
      ]);
      // Quick Picks is a Discover entry point, never a fourth slot.
      expect(links).toHaveLength(3);
      expect(links[0]).toHaveAttribute("href", `/discover?userId=${PERSONA}`);
    }

    expect(await axe(container)).toHaveNoViolations();
  });

  it("names the persona rather than printing its ID", () => {
    renderShell();

    expect(screen.getByText("Exploring as")).toBeVisible();
    expect(screen.getByText("Eclectic Viewer")).toBeVisible();
    expect(screen.queryByText(new RegExp(String(PERSONA)))).not.toBeInTheDocument();
  });

  it("keeps the signed-in actor separate from the selected persona", () => {
    renderShell();

    // The two identities are different, and a portfolio persona must never
    // read as the signed-in human's private account.
    expect(screen.getByText("Signed in as")).toBeVisible();
    expect(screen.getByText("demo")).toBeVisible();
  });

  /**
   * Both identity lines used to be `display: none` below 1050px, which removed
   * the labelled spans from the accessibility tree as well as from the screen
   * and left an `aria-hidden` two-letter dot as the only answer to "whose data
   * is this" on a phone. The markup is now width-independent — only the layout
   * moves — so this is the assertion that keeps it that way; the browser matrix
   * in `e2e/shell-identity.spec.ts` checks the rendered result at each width.
   */
  it("renders each identity once, in its own labelled node", () => {
    renderShell();

    const actorLabel = screen.getByText("Signed in as");
    const personaLabel = screen.getByText("Exploring as");
    const actorName = screen.getByText("demo");
    const personaName = screen.getByText("Eclectic Viewer");

    // Exactly one of each: a second copy for a second breakpoint would read as
    // two identities to anyone using the accessibility tree.
    expect(screen.queryAllByText("Signed in as")).toHaveLength(1);
    expect(screen.queryAllByText("Exploring as")).toHaveLength(1);

    expect(actorName).not.toBe(personaName);
    expect(actorLabel.parentElement).toBe(actorName.parentElement);
    expect(personaLabel.parentElement).toBe(personaName.parentElement);
    // The actor block never contains the persona, and the reverse — which is
    // the one thing the design contract forbids outright.
    expect(actorName.parentElement).not.toContainElement(personaName);
    expect(personaName.parentElement).not.toContainElement(actorName);
  });

  it("leaves the initials out of the accessibility tree, because the name is beside them", () => {
    const { container } = renderShell();
    const dot = container.querySelector(".persona-dot");

    expect(dot).toHaveTextContent("EV");
    expect(dot).toHaveAttribute("aria-hidden", "true");
  });

  it("labels the fixture harness as isolated rather than as a signed-in session", () => {
    renderShell({ actorName: "Fixture reviewer", fixtureMode: true, legacyHref: undefined });

    expect(screen.getByText("Isolated mode")).toBeVisible();
    expect(screen.queryByText("Signed in as")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Exit preview" })).toBeVisible();
  });

  it("offers the legacy dashboard as a footer utility link, not a nav slot", () => {
    renderShell();

    const legacy = screen.getByRole("link", { name: "Legacy dashboard" });
    expect(legacy).toHaveAttribute("href", "/legacy");
    expect(
      within(screen.getByRole("navigation", { name: "Primary" })).queryByRole("link", {
        name: "Legacy dashboard",
      }),
    ).not.toBeInTheDocument();
  });

  it("omits the legacy link where it would only lead to the sign-in door", () => {
    // The fixture harness has no session, so /legacy would redirect a reviewer
    // straight back out of the preview.
    renderShell({ fixtureMode: true, legacyHref: undefined });

    expect(screen.queryByRole("link", { name: "Legacy dashboard" })).not.toBeInTheDocument();
  });

  /**
   * The TMDB notice.
   *
   * It lived only on `/legacy` until this, so the five product routes showed
   * TMDB posters, backdrops, scores and cast with no attribution anywhere —
   * and retiring the dashboard would have removed the app's only copy. Putting
   * it in the shell is what makes it a property of the product; asserting it
   * here is what stops the next shell edit from dropping it.
   */
  it("carries the TMDB notice for every route that renders it", () => {
    renderShell();

    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent(
      "This product uses the TMDB API but is not endorsed or certified by TMDB.",
    );
    expect(within(footer).getByRole("link", { name: "TMDB" })).toHaveAttribute(
      "href",
      "https://www.themoviedb.org",
    );
  });

  it("keeps the notice where there is no legacy link to hang a footer on", () => {
    // The footer used to exist only when a route passed `legacyHref`, which is
    // exactly the case the fixture preview and fixture-mode Discover do not.
    renderShell({ fixtureMode: true, legacyHref: undefined });

    expect(screen.getByRole("contentinfo")).toHaveTextContent(
      "This product uses the TMDB API but is not endorsed or certified by TMDB.",
    );
  });

  it("gives every route one main landmark and a skip link into it", () => {
    // Quick Picks rendered outside this shell until the sweep, so it had
    // neither. Asserting them here is what makes them a property of the
    // product rather than of four routes out of five.
    renderShell();

    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });
});
