import { fireEvent, render, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";

import { PosterCard } from "@/components/movie/poster-card";
import { movies } from "@/lib/fixtures/movie-fixtures";

/** The card's poster, whatever it currently is: an image or the mark. */
function posterImage(container: HTMLElement): HTMLImageElement | null {
  return container.querySelector("img");
}

describe("PosterCard", () => {
  it("reserves the poster and renders a named fallback when artwork is missing", async () => {
    const { container } = render(<PosterCard movie={movies[8]} />);

    expect(screen.getByTestId("poster-fallback")).toHaveTextContent("Artwork unavailable");
    expect(screen.getByRole("link", { name: `Open ${movies[8].title}` })).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("falls back after an image error without losing movie identity", () => {
    const { container } = render(<PosterCard movie={movies[0]} />);
    fireEvent.error(posterImage(container)!);

    expect(screen.getByTestId("poster-fallback")).toBeVisible();
    expect(screen.getByText(movies[0].title)).toBeVisible();
  });

  it("forgets the failure when a different movie takes the same slot", () => {
    // The featured slot and the rails re-render in place, so a broken poster
    // used to make every movie that landed there afterwards look artless.
    const { container, rerender } = render(<PosterCard movie={movies[0]} />);
    fireEvent.error(posterImage(container)!);
    expect(screen.getByTestId("poster-fallback")).toBeVisible();

    rerender(<PosterCard movie={movies[1]} />);

    expect(screen.queryByTestId("poster-fallback")).not.toBeInTheDocument();
    expect(posterImage(container)).toBeInTheDocument();
    expect(screen.getByText(movies[1].title)).toBeVisible();
  });

  it("keeps the fallback if the same movie comes back after failing", () => {
    const { container, rerender } = render(<PosterCard movie={movies[0]} />);
    fireEvent.error(posterImage(container)!);

    rerender(<PosterCard movie={{ ...movies[0], rank: 3 }} />);

    expect(screen.getByTestId("poster-fallback")).toBeVisible();
  });

  it("shows the shared mark, derived from the title it displays", () => {
    render(<PosterCard movie={{ ...movies[8], title: "Sense and Sensibility" }} />);

    expect(within(screen.getByTestId("poster-fallback")).getByText("SS")).toBeInTheDocument();
  });

  it("offers the movie as one link rather than two to the same place", () => {
    // Five tab stops per card is what a rail of nine costs a keyboard viewer;
    // the poster and its title are one destination and now one target.
    render(<PosterCard href="/movies/101" movie={movies[0]} />);

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAccessibleName(`Open ${movies[0].title}`);
    expect(links[0]).toHaveAttribute("href", "/movies/101");
    expect(within(links[0]).getByText(movies[0].title)).toBeVisible();
  });
});
