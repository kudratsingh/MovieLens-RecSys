import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { StateControls } from "@/components/movie/state-controls";
import { movies } from "@/lib/fixtures/movie-fixtures";

it("exposes watched and watchlist state through keyboard-operable pressed buttons", async () => {
  const user = userEvent.setup();
  render(<StateControls initialState={movies[1].state} title={movies[1].title} />);
  const watchlist = screen.getByRole("button", { name: "Watchlist" });

  watchlist.focus();
  await user.keyboard("{Enter}");

  expect(screen.getByRole("button", { name: "In watchlist" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText(/marked as in watchlist.*Preview only/i)).toBeInTheDocument();
});
