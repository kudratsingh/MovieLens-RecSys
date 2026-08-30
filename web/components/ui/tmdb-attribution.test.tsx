import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import {
  TMDB_ATTRIBUTION_NOTICE,
  TmdbAttribution,
} from "@/components/ui/tmdb-attribution";

/**
 * The notice TMDB's terms require.
 *
 * This is the one component in the product whose exact words are an
 * obligation rather than a design choice, so the wording is asserted verbatim
 * — a well-meant rewrite is the failure mode, not a missing element.
 */
describe("the TMDB attribution", () => {
  it("states the required sentence verbatim", () => {
    render(<TmdbAttribution />);

    expect(
      screen.getByText(
        "This product uses the TMDB API but is not endorsed or certified by TMDB.",
      ),
    ).toBeVisible();
    // The constant and the sentence above are checked against each other, so
    // editing one without the other fails here rather than in a browser.
    expect(TMDB_ATTRIBUTION_NOTICE).toBe(
      "This product uses the TMDB API but is not endorsed or certified by TMDB.",
    );
  });

  it("links the mark to themoviedb.org, and opens it safely", () => {
    render(<TmdbAttribution />);

    const link = screen.getByRole("link", { name: "TMDB" });
    expect(link).toHaveAttribute("href", "https://www.themoviedb.org");
    expect(link).toHaveAttribute("target", "_blank");
    // `noopener` is the half that matters: a `_blank` target hands the opened
    // page a `window.opener` handle back into this one without it.
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("carries the mark as an image with alternative text", () => {
    render(<TmdbAttribution />);

    const logo = screen.getByRole("img", { name: "TMDB" });
    expect(logo).toHaveAttribute("src", expect.stringContaining("tmdb-logo.svg"));
    // The contracted inline size. A logo scaled up would start competing with
    // the product's own wordmark.
    expect(logo).toHaveAttribute("width", "100");
    expect(logo).toHaveAttribute("height", "13");
  });

  it("prefixes a scope without dropping the required sentence", () => {
    render(<TmdbAttribution lead="Details from TMDB." />);

    expect(
      screen.getByText(`Details from TMDB. ${TMDB_ATTRIBUTION_NOTICE}`),
    ).toBeVisible();
  });

  it("lets each placement own its layout class", () => {
    const { container } = render(
      <TmdbAttribution className="movie-detail-attribution" />,
    );

    expect(container.querySelector(".movie-detail-attribution")).not.toBeNull();
    expect(container.querySelector(".tmdb-attribution")).toBeNull();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<TmdbAttribution />);

    expect(await axe(container)).toHaveNoViolations();
  });
});
