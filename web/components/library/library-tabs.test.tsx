import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { LibraryTabs } from "@/components/library/library-tabs";

const COUNTS = { history: 15, rated: 12, watchlist: 4 };

describe("library tabs", () => {
  it("names each collection with its server-provided count", async () => {
    const { container } = render(
      <>
        <LibraryTabs active="rated" counts={COUNTS} onSelect={vi.fn()} />
        <div aria-labelledby="library-tab-rated" id="library-panel-rated" role="tabpanel">
          Rated movies
        </div>
      </>,
    );

    expect(screen.getByRole("tab", { name: "Rated 12" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Watchlist 4" })).toBeVisible();
    // The tab's identity stays `history` in the URL, the API, and the type;
    // only what the reader sees became `Seen`.
    expect(screen.getByRole("tab", { name: "Seen 15" })).toBeVisible();
    expect(screen.queryByRole("tab", { name: /History/ })).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows no count at all rather than inventing a zero before the first read", () => {
    render(<LibraryTabs active="rated" counts={null} onSelect={vi.fn()} />);

    expect(screen.getByRole("tab", { name: "Rated" })).toBeVisible();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("moves between collections with the arrow keys, and takes focus along", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { rerender } = render(
      <LibraryTabs active="rated" counts={COUNTS} onSelect={onSelect} />,
    );

    await user.tab();
    expect(screen.getByRole("tab", { name: "Rated 12" })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(onSelect).toHaveBeenLastCalledWith("watchlist");
    expect(screen.getByRole("tab", { name: "Watchlist 4" })).toHaveFocus();

    rerender(<LibraryTabs active="watchlist" counts={COUNTS} onSelect={onSelect} />);
    await user.keyboard("{End}");
    expect(onSelect).toHaveBeenLastCalledWith("history");

    rerender(<LibraryTabs active="history" counts={COUNTS} onSelect={onSelect} />);
    await user.keyboard("{Home}");
    expect(onSelect).toHaveBeenLastCalledWith("rated");
  });

  it("keeps unselected tabs out of the tab sequence", () => {
    render(<LibraryTabs active="watchlist" counts={COUNTS} onSelect={vi.fn()} />);

    expect(screen.getByRole("tab", { name: "Watchlist 4" })).toHaveAttribute(
      "tabindex",
      "0",
    );
    expect(screen.getByRole("tab", { name: "Rated 12" })).toHaveAttribute(
      "tabindex",
      "-1",
    );
  });
});
