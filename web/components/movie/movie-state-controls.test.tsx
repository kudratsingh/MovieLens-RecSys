import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { libraryControlSet } from "@/components/library/library-row";
import {
  DETAIL_CONTROLS,
  MovieRatingControl,
  MovieStateControls,
  PREVIEW_CONTROLS,
  RECOMMENDATION_CONTROLS,
} from "@/components/movie/movie-state-controls";
import { movies } from "@/lib/fixtures/movie-fixtures";
import { UNKNOWN_MOVIE_STATE } from "@/lib/movie-state/actions";

const unknown = UNKNOWN_MOVIE_STATE;

const confirmation = {
  trigger: "Remove from history",
  action: "Remove from history",
  groupLabel: "Confirm removing Heat from watched history",
  consequence: "Removing Heat from history deletes the watched interaction.",
};

describe("the preview configuration toggles locally and says so", () => {
  it("exposes watched and watchlist state through keyboard-operable pressed buttons", async () => {
    const user = userEvent.setup();
    render(
      <MovieStateControls
        controls={PREVIEW_CONTROLS}
        initialState={movies[1].state}
        title={movies[1].title}
      />,
    );
    const watchlist = screen.getByRole("button", { name: "Watchlist" });

    watchlist.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("button", { name: "In watchlist" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByText(/marked as in watchlist.*Preview only/i),
    ).toBeInTheDocument();
  });
});

describe("live mode reports intent and lets the caller own the mutation", () => {
  it("does not claim a change of its own", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <MovieStateControls
        controls={RECOMMENDATION_CONTROLS}
        idPrefix="live"
        onAction={onAction}
        state={unknown}
        title="Heat"
      />,
    );

    await user.click(screen.getByRole("button", { name: "Watchlist" }));

    expect(onAction).toHaveBeenCalledWith(
      { resource: "watchlist", method: "PUT" },
      expect.objectContaining({ id: "live-watchlist" }),
    );
    // Nothing moved: the canonical state still says the movie is not saved.
    expect(screen.getByRole("button", { name: "Watchlist" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("sends the removing method when the canonical state already holds the value", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <MovieStateControls
        controls={RECOMMENDATION_CONTROLS}
        onAction={onAction}
        state={{ ...unknown, watchlisted: true }}
        title="Heat"
      />,
    );

    await user.click(screen.getByRole("button", { name: "In watchlist" }));

    expect(onAction.mock.calls[0][0]).toEqual({
      resource: "watchlist",
      method: "DELETE",
    });
  });

  it("offers dismissal only where a surface declares it, and calls it an exclusion", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const { rerender } = render(
      <MovieStateControls
        controls={PREVIEW_CONTROLS}
        onAction={onAction}
        state={unknown}
        title="Heat"
      />,
    );
    expect(screen.queryByRole("button", { name: "Not for me" })).not.toBeInTheDocument();

    rerender(
      <MovieStateControls
        controls={RECOMMENDATION_CONTROLS}
        onAction={onAction}
        state={unknown}
        title="Heat"
      />,
    );
    await user.click(screen.getByRole("button", { name: "Not for me" }));

    expect(onAction.mock.calls[0][0]).toEqual({
      resource: "dismissal",
      method: "PUT",
    });
  });

  it("does not offer a destructive history removal beside a recommendation", () => {
    render(
      <MovieStateControls
        controls={RECOMMENDATION_CONTROLS}
        onAction={vi.fn()}
        state={{ ...unknown, watched: true }}
        title="Heat"
      />,
    );

    const watched = screen.getByRole("button", { name: "Watched" });
    expect(watched).toHaveAttribute("aria-disabled", "true");
    expect(watched).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: /Remove/ })).not.toBeInTheDocument();
  });

  it("refuses every control while a mutation is in flight without losing focus", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <MovieStateControls
        busy
        controls={RECOMMENDATION_CONTROLS}
        onAction={onAction}
        pending="watchlist"
        state={unknown}
        title="Heat"
      />,
    );

    for (const name of ["Watchlist", "Mark watched", "Not for me"]) {
      expect(screen.getByRole("button", { name })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    }
    expect(screen.getByRole("button", { name: "Watchlist" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    // `aria-disabled` keeps the control focusable, which is what lets a failed
    // write put the reader back on it — but it must not submit.
    await user.click(screen.getByRole("button", { name: "Watchlist" }));
    expect(onAction).not.toHaveBeenCalled();
  });
});

describe("each surface declares its own hierarchy", () => {
  it("leads a Watchlist row with the action that moves the movie forward", () => {
    render(
      <MovieStateControls
        controls={libraryControlSet("watchlist", false)}
        onAction={vi.fn()}
        state={{ ...unknown, watchlisted: true }}
        title="Heat"
      />,
    );

    const labels = screen
      .getAllByRole("button")
      .map((button) => button.textContent?.trim());
    expect(labels).toEqual([
      "Mark watched",
      "Remove from watchlist",
      "Not for me",
    ]);
  });

  it("shows only the undo on a collection that does not own dismissal", () => {
    const { rerender } = render(
      <MovieStateControls
        controls={libraryControlSet("rated", true)}
        onAction={vi.fn()}
        state={{ ...unknown, watched: true }}
        title="Heat"
      />,
    );
    expect(screen.queryByRole("button", { name: "Not for me" })).not.toBeInTheDocument();

    rerender(
      <MovieStateControls
        controls={libraryControlSet("rated", true)}
        onAction={vi.fn()}
        state={{ ...unknown, watched: true, dismissed: true }}
        title="Heat"
      />,
    );
    expect(screen.getByRole("button", { name: "Undo not for me" })).toBeVisible();
  });

  it("leads movie detail with Watchlist while the movie is unseen", () => {
    render(
      <MovieStateControls
        confirmation={confirmation}
        controls={DETAIL_CONTROLS}
        onAction={vi.fn()}
        state={unknown}
        title="Heat"
      />,
    );

    const labels = screen
      .getAllByRole("button")
      .map((button) => button.textContent?.trim());
    expect(labels).toEqual(["Watchlist", "Mark watched", "Not for me"]);
    // Saved is the accented state, so the accent has to move with it.
    expect(screen.getByRole("button", { name: "Watchlist" })).toHaveClass(
      "button-secondary",
    );
  });
});

describe("removing watched history is confirmed once, everywhere", () => {
  it("states the consequence, writes nothing until confirmed, and is escapable", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const { container } = render(
      <MovieStateControls
        confirmation={confirmation}
        controls={libraryControlSet("history", true)}
        idPrefix="row"
        onAction={onAction}
        state={{ ...unknown, watched: true }}
        title="Heat"
      />,
    );

    await user.click(screen.getByRole("button", { name: "Remove from history" }));

    const group = screen.getByRole("group", {
      name: "Confirm removing Heat from watched history",
    });
    expect(group).toHaveTextContent("deletes the watched interaction");
    expect(onAction).not.toHaveBeenCalled();
    expect(await axe(container)).toHaveNoViolations();

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remove from history" })).toHaveFocus(),
    );
    expect(onAction).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Remove from history" }));
    await user.click(
      within(
        screen.getByRole("group", { name: /^Confirm removing/ }),
      ).getByRole("button", { name: "Remove from history" }),
    );

    expect(onAction.mock.calls[0][0]).toEqual({
      resource: "watched",
      method: "DELETE",
    });
  });
});

describe("the rating editor says the same thing in both input shapes", () => {
  it("names each star for the movie it rates and offers a clear", async () => {
    const user = userEvent.setup();
    const onRate = vi.fn();
    const { container } = render(
      <MovieRatingControl
        clearLabel="Clear rating"
        note="Star magnitude is display feedback today."
        onRate={onRate}
        rating={4}
        title="Heat"
      />,
    );

    const panel = screen.getByRole("group", { name: "Your rating" });
    expect(within(panel).getByRole("button", { name: "1 star for Heat" })).toBeVisible();
    expect(within(panel).getByRole("button", { name: "4 stars for Heat" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(await axe(container)).toHaveNoViolations();

    await user.click(screen.getByRole("button", { name: "2 stars for Heat" }));
    expect(onRate.mock.calls[0][0]).toBe(2);

    await user.click(screen.getByRole("button", { name: "Clear rating" }));
    expect(onRate.mock.calls[1][0]).toBeNull();
    // The clear button unmounts with the value it removed, so focus recovery is
    // handed the editor that survives instead.
    expect(onRate.mock.calls[1][1]).toBe(
      screen.getByRole("button", { name: "1 star for Heat" }),
    );
  });

  it("reports the recorded value, because it has no acknowledgement of its own", () => {
    const { rerender } = render(
      <MovieRatingControl onRate={vi.fn()} rating={null} title="Heat" />,
    );
    expect(screen.getByText("Not rated")).toBeVisible();

    rerender(<MovieRatingControl onRate={vi.fn()} rating={4} title="Heat" />);
    expect(screen.getByText("4 out of 5 recorded")).toBeVisible();
  });

  it("offers the stored half-star precision where a value is being edited", async () => {
    const user = userEvent.setup();
    const onRate = vi.fn();
    const { container } = render(
      <MovieRatingControl
        clearLabel="Remove rating"
        idPrefix="library-103"
        mode="half-star-select"
        onRate={onRate}
        rating={4.5}
        title="Heat"
      />,
    );

    const select = screen.getByLabelText("Rating for Heat");
    expect(select).toHaveValue("4.5");
    expect(select).toHaveAttribute("id", "library-103-rating");
    expect(await axe(container)).toHaveNoViolations();

    await user.selectOptions(select, "3");
    expect(onRate.mock.calls[0][0]).toBe(3);

    await user.click(screen.getByRole("button", { name: "Remove rating" }));
    expect(onRate.mock.calls[1][0]).toBeNull();
    expect(onRate.mock.calls[1][1]).toBe(select);
  });
});

describe("rail density shortens the word, never the action", () => {
  it("keeps the full action as the accessible name while the visible label fits one line", () => {
    const { rerender } = render(
      <MovieStateControls
        compact
        controls={RECOMMENDATION_CONTROLS}
        idPrefix="rail-6"
        onAction={vi.fn()}
        state={unknown}
        title="Heat"
      />,
    );

    // `Mark watched` is what a screen reader, speech input, and every journey
    // that asks for this control by name still get; `Watched` is only what the
    // pill has room to print at a rail card's width.
    const watched = screen.getByRole("button", { name: "Mark watched" });
    expect(watched).toHaveTextContent("Watched");
    expect(watched).toHaveAttribute("aria-pressed", "false");

    rerender(
      <MovieStateControls
        compact
        controls={RECOMMENDATION_CONTROLS}
        idPrefix="rail-6"
        onAction={vi.fn()}
        state={{ ...unknown, watchlisted: true }}
        title="Heat"
      />,
    );

    const saved = screen.getByRole("button", { name: "In watchlist" });
    // The visible word does not change with the state, so the pill cannot
    // change width and the row cannot jiggle under the viewer.
    expect(saved).toHaveTextContent("Watchlist");
    expect(saved).toHaveAttribute("aria-pressed", "true");
    expect(saved).toHaveClass("movie-state-on");
  });

  it("leaves every other surface's labels exactly as they were", () => {
    render(
      <MovieStateControls
        controls={RECOMMENDATION_CONTROLS}
        idPrefix="featured-6"
        onAction={vi.fn()}
        state={{ ...unknown, watchlisted: true }}
        title="Heat"
      />,
    );

    const labels = screen
      .getAllByRole("button")
      .map((button) => button.textContent?.trim());
    expect(labels).toEqual(["In watchlist", "Mark watched", "Not for me"]);
    expect(screen.getByRole("button", { name: "Mark watched" })).not.toHaveAttribute(
      "aria-label",
    );
  });

  it("marks a recorded watch as recorded rather than as still in flight", async () => {
    const { container } = render(
      <MovieStateControls
        compact
        controls={RECOMMENDATION_CONTROLS}
        idPrefix="rail-7"
        onAction={vi.fn()}
        state={{ ...unknown, watched: true }}
        title="Heat"
      />,
    );

    const watched = screen.getByRole("button", { name: "Watched" });
    expect(watched).toHaveAttribute("aria-disabled", "true");
    expect(watched).toHaveAttribute("aria-busy", "false");
    // The class the stylesheet keys the non-faded, non-`progress` look off.
    expect(watched).toHaveClass("movie-state-recorded");
    expect(await axe(container)).toHaveNoViolations();
  });
});
