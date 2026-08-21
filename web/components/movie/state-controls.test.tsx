import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { StateControls } from "@/components/movie/state-controls";
import { movies } from "@/lib/fixtures/movie-fixtures";

const unknown = { watched: false, watchlisted: false, rating: null, suppressed: false };

it("exposes watched and watchlist state through keyboard-operable pressed buttons", async () => {
  const user = userEvent.setup();
  render(<StateControls initialState={movies[1].state} title={movies[1].title} />);
  const watchlist = screen.getByRole("button", { name: "Watchlist" });

  watchlist.focus();
  await user.keyboard("{Enter}");

  expect(screen.getByRole("button", { name: "In watchlist" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText(/marked as in watchlist.*Preview only/i)).toBeInTheDocument();
});

describe("live mode reports intent and lets the caller own the mutation", () => {
  it("does not claim a change of its own", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    render(
      <StateControls
        idPrefix="live"
        initialState={unknown}
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
      <StateControls
        initialState={unknown}
        onAction={onAction}
        state={{ ...unknown, watchlisted: true }}
        title="Heat"
      />,
    );

    await user.click(screen.getByRole("button", { name: "In watchlist" }));

    expect(onAction.mock.calls[0][0]).toEqual({ resource: "watchlist", method: "DELETE" });
  });

  it("offers dismissal only where a route opts in, and calls it an exclusion", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const { rerender } = render(
      <StateControls initialState={unknown} onAction={onAction} state={unknown} title="Heat" />,
    );
    expect(screen.queryByRole("button", { name: "Not for me" })).not.toBeInTheDocument();

    rerender(
      <StateControls
        initialState={unknown}
        onAction={onAction}
        showDismiss
        state={unknown}
        title="Heat"
      />,
    );
    await user.click(screen.getByRole("button", { name: "Not for me" }));

    expect(onAction.mock.calls[0][0]).toEqual({ resource: "dismissal", method: "PUT" });
  });

  it("does not offer a destructive history removal beside a recommendation", () => {
    render(
      <StateControls
        initialState={unknown}
        onAction={vi.fn()}
        state={{ ...unknown, watched: true }}
        title="Heat"
      />,
    );

    const watched = screen.getByRole("button", { name: "Watched" });
    expect(watched).toBeDisabled();
    expect(watched).toHaveAttribute("aria-pressed", "true");
  });

  it("disables every control while a mutation is in flight", () => {
    render(
      <StateControls
        busy
        initialState={unknown}
        onAction={vi.fn()}
        showDismiss
        state={unknown}
        title="Heat"
      />,
    );

    for (const name of ["Watchlist", "Mark watched", "Not for me"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });
});
