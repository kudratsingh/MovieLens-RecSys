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
});
