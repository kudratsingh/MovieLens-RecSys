import { fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";

import { MovieRail } from "@/components/movie/movie-rail";
import { movies } from "@/lib/fixtures/movie-fixtures";

function renderRail() {
  return render(
    <MovieRail
      movies={movies.slice(0, 3)}
      seeAllHref="/browse"
      title="Because you watched"
    />,
  );
}

describe("MovieRail", () => {
  it("names the track and gives it a role instead of a bare focusable div", async () => {
    const { container } = renderRail();

    const track = screen.getByRole("group", { name: "Because you watched movies" });
    expect(track).toHaveAttribute("tabindex", "0");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("cancels the browser's own scroll so one arrow press moves the track once", () => {
    renderRail();
    const track = screen.getByRole("group", { name: "Because you watched movies" });
    // jsdom does not scroll, so the assertion is on the default action being
    // cancelled — the native scroll running alongside `move()` is what made a
    // single press travel roughly two screens.
    const scrollBy = vi.spyOn(track, "scrollBy").mockImplementation(() => {});

    const right = fireEvent.keyDown(track, { key: "ArrowRight" });
    const left = fireEvent.keyDown(track, { key: "ArrowLeft" });
    const unrelated = fireEvent.keyDown(track, { key: "ArrowDown" });

    // fireEvent returns false when a listener called preventDefault.
    expect(right).toBe(false);
    expect(left).toBe(false);
    expect(unrelated).toBe(true);
    expect(scrollBy).toHaveBeenCalledTimes(2);
  });

  it("costs one link per card", () => {
    renderRail();

    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(3);
    for (const card of cards) {
      expect(card.querySelectorAll("a")).toHaveLength(1);
    }
  });
});
