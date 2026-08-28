import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { expect, it, vi } from "vitest";

import { FrontendErrorBoundary } from "@/components/ui/error-boundary";
import {
  EmptyState,
  ErrorState,
  ResourceBlock,
} from "@/components/ui/resource-states";
import { recordedResource } from "@/lib/fixtures/movie-fixtures";

it("keeps a healthy resource visible when a sibling resource fails", async () => {
  const healthy = recordedResource("catalog", ["Catalog is here"]);
  const failed = recordedResource("evidence", ["Evidence"], ["evidence"]);
  const { container } = render(
    <main>
      <ResourceBlock label="Catalog" result={healthy}>{(items) => <p>{items[0]}</p>}</ResourceBlock>
      <ResourceBlock label="Evidence" result={failed}>{(items) => <p>{items[0]}</p>}</ResourceBlock>
    </main>,
  );

  expect(screen.getByText("Catalog is here")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("Evidence is taking a night off");
  expect(await axe(container)).toHaveNoViolations();
});

it("offers a retry only when the caller can actually run one", async () => {
  const user = userEvent.setup();
  const onRetry = vi.fn();

  const inert = render(<ErrorState label="Evidence" message="It failed." />);
  // A button with no handler is worse than no button: it is the one thing the
  // viewer can press, and it does nothing.
  expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  inert.unmount();

  render(<ErrorState label="Evidence" message="It failed." onRetry={onRetry} />);
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
});

it("re-runs a subtree that failed to render", async () => {
  const user = userEvent.setup();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  let shouldThrow = true;
  function Fragile() {
    if (shouldThrow) throw new Error("render failed");
    return <p>Recovered</p>;
  }

  render(
    <FrontendErrorBoundary label="Recommendations">
      <Fragile />
    </FrontendErrorBoundary>,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Recommendations is taking a night off");

  shouldThrow = false;
  await user.click(screen.getByRole("button", { name: "Try again" }));
  expect(screen.getByText("Recovered")).toBeVisible();
  consoleError.mockRestore();
});

it("keeps several ways out in one row, in the order they were offered", async () => {
  const { container } = render(
    <main>
      <EmptyState
        action={
          <>
            <a className="button-primary" href="/browse">
              Browse the catalog
            </a>
            <a className="button-quiet" href="/quick-picks">
              Try Quick Picks
            </a>
          </>
        }
        message="Nothing is ranked for this persona yet."
        title="No recommendations right now"
      />
    </main>,
  );

  const actions = container.querySelector(".resource-state-actions");
  expect(actions).not.toBeNull();
  const offered = within(actions as HTMLElement)
    .getAllByRole("link")
    .map((link) => link.textContent);
  expect(offered).toEqual(["Browse the catalog", "Try Quick Picks"]);
  expect(await axe(container)).toHaveNoViolations();
});

it("renders no action row when a state has no way out to offer", () => {
  const { container } = render(
    <EmptyState message="Nothing here." title="No watch history yet" />,
  );

  // The element is still emitted; `:empty` hides it, so an absent action does
  // not leave a gap in the block.
  expect(container.querySelector(".resource-state-actions")).toBeEmptyDOMElement();
});
