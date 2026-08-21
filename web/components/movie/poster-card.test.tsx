import { fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";

import { PosterCard } from "@/components/movie/poster-card";
import { movies } from "@/lib/fixtures/movie-fixtures";

describe("PosterCard", () => {
  it("reserves the poster and renders a named fallback when artwork is missing", async () => {
    const { container } = render(<PosterCard movie={movies[8]} />);

    expect(screen.getByTestId("poster-fallback")).toHaveTextContent("Artwork unavailable");
    expect(screen.getByRole("link", { name: `Open ${movies[8].title}` })).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("falls back after an image error without losing movie identity", () => {
    render(<PosterCard movie={movies[0]} />);
    fireEvent.error(screen.getByAltText(movies[0].posterAlt));

    expect(screen.getByTestId("poster-fallback")).toBeVisible();
    expect(screen.getByText(movies[0].title)).toBeVisible();
  });
});
