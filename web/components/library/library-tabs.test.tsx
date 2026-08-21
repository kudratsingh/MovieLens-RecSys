import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";

import { LibraryTabs } from "@/components/library/library-tabs";
import { libraryFixture } from "@/lib/fixtures/movie-fixtures";

it("moves between distinct library collections without losing its tab semantics", async () => {
  const user = userEvent.setup();
  const { container } = render(<LibraryTabs collection={libraryFixture} />);

  await user.click(screen.getByRole("tab", { name: /Watchlist/ }));

  expect(screen.getByRole("tab", { name: /Watchlist/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel", { name: /Watchlist/ })).toBeVisible();
  expect(await axe(container)).toHaveNoViolations();
});
