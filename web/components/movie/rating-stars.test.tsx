import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CELEBRATION_MS,
  COLLAPSE_MS,
  RatingStars,
} from "@/components/movie/rating-stars";

const TITLE = "The Handmaiden";

type RatingStarsProps = Parameters<typeof RatingStars>[0];

function renderStars(props: Partial<Omit<RatingStarsProps, "onRate">> = {}) {
  const onRate = vi.fn<RatingStarsProps["onRate"]>();
  const view = render(
    <RatingStars
      clearLabel="Clear rating"
      idPrefix="rating"
      note="Rating this records a watch; the star value is display feedback today, not a graded training signal."
      onRate={onRate}
      rating={null}
      title={TITLE}
      {...props}
    />,
  );
  return { ...view, onRate };
}

function star(value: number) {
  const unit = value === 1 ? "star" : "stars";
  return screen.getByRole("button", { name: `${value} ${unit} for ${TITLE}` });
}

/** jsdom has no `matchMedia`, so the setting has to be installed to be read. */
function setReducedMotion(reduce: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }),
  });
}

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(window, "matchMedia");
});

describe("the open row", () => {
  it("names every star and reports the value the viewer chose", async () => {
    const { container, onRate } = renderStars();

    expect(screen.getByRole("group", { name: "Your rating" })).toBeVisible();
    expect(screen.getByRole("button", { name: `1 star for ${TITLE}` })).toBeVisible();

    await userEvent.click(star(4));

    expect(onRate).toHaveBeenCalledTimes(1);
    expect(onRate.mock.calls[0][0]).toBe(4);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("fills from the left on hover and empties again on leave", async () => {
    renderStars();

    await userEvent.hover(star(3));
    expect(star(1)).toHaveClass("is-filled");
    expect(star(3)).toHaveClass("is-filled");
    expect(star(4)).not.toHaveClass("is-filled");

    await userEvent.unhover(star(3));
    expect(star(1)).not.toHaveClass("is-filled");
  });

  it("is one tab stop, moved with the arrow keys and committed with Enter", async () => {
    const { onRate } = renderStars();

    // The row has a single tab stop, not five: tabbing through every star to
    // reach `Clear rating` was five stops for one decision.
    expect(star(1)).toHaveAttribute("tabindex", "0");
    expect(star(2)).toHaveAttribute("tabindex", "-1");

    await userEvent.tab();
    expect(star(1)).toHaveFocus();

    await userEvent.keyboard("{ArrowRight}{ArrowRight}");
    expect(star(3)).toHaveFocus();
    // The preview follows the focus, so a keyboard viewer sees the value before
    // committing it exactly as a pointer viewer does.
    expect(star(3)).toHaveClass("is-filled");
    expect(star(4)).not.toHaveClass("is-filled");

    await userEvent.keyboard("{End}");
    expect(star(5)).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(star(1)).toHaveFocus();

    await userEvent.keyboard("{ArrowRight}{Enter}");
    expect(onRate).toHaveBeenCalledTimes(1);
    expect(onRate.mock.calls[0][0]).toBe(2);
  });

  it("offers Clear rating only once there is a value to clear", async () => {
    const { onRate, rerender } = renderStars();
    expect(screen.queryByRole("button", { name: "Clear rating" })).not.toBeInTheDocument();

    setReducedMotion(true);
    rerender(
      <RatingStars
        clearLabel="Clear rating"
        idPrefix="rating"
        onRate={onRate}
        rating={4}
        title={TITLE}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: `Change rating for ${TITLE}` }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Clear rating" }));
    expect(onRate).toHaveBeenCalledWith(null, expect.anything());
  });

  it("reports nothing while a write is in flight", async () => {
    const { onRate } = renderStars({ busy: true });

    await userEvent.click(star(4));
    expect(onRate).not.toHaveBeenCalled();
    expect(star(4)).toHaveAttribute("aria-disabled", "true");
  });

  it("fills to the pending value without acknowledging it", () => {
    // The optimistic frame answers the press; the celebration waits for the
    // commit, because a write that rolls back must not have been celebrated.
    renderStars({ pendingRating: 4 });

    expect(star(4)).toHaveClass("is-filled");
    expect(star(4)).not.toHaveClass("is-chosen");
    expect(screen.queryByText("You rated 4/5")).not.toBeInTheDocument();
  });
});

describe("the commit acknowledgement", () => {
  it("fills, pops, then collapses into the chip", async () => {
    vi.useFakeTimers();
    const { rerender } = renderStars();

    // The caller reports the committed value; that is what starts the sequence.
    rerender(
      <RatingStars idPrefix="rating" onRate={vi.fn()} rating={4} title={TITLE} />,
    );

    // Stars up to the chosen one animate; the chosen one alone gets the pop.
    expect(star(1)).toHaveClass("is-committing");
    expect(star(4)).toHaveClass("is-committing", "is-chosen");
    expect(star(5)).not.toHaveClass("is-committing");
    // The stagger is expressed as a per-star index, so one delay expression
    // drives the fill, the pop, and the glow together.
    expect(star(4)).toHaveStyle({ "--star-index": "3" });

    act(() => {
      vi.advanceTimersByTime(CELEBRATION_MS);
    });
    expect(screen.getByRole("button", { name: `4 stars for ${TITLE}` })).toBeVisible();

    act(() => {
      vi.advanceTimersByTime(COLLAPSE_MS);
    });
    expect(screen.getByText("You rated 4/5")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: `4 stars for ${TITLE}` }),
    ).not.toBeInTheDocument();
  });

  it("announces the result politely without a second status region", async () => {
    setReducedMotion(true);
    const { container, rerender } = renderStars();

    rerender(
      <RatingStars idPrefix="rating" onRate={vi.fn()} rating={5} title={TITLE} />,
    );

    const live = container.querySelector('[aria-live="polite"]');
    expect(live).toHaveTextContent(
      `Rated 5 out of 5 for ${TITLE}. Use Change rating to edit it.`,
    );
    // The panel around this owns the status region carrying what the API
    // committed. Two status roles in one panel is how one of them stops being
    // read at all.
    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps focus with the control when the row it was in collapses", () => {
    vi.useFakeTimers();
    const { rerender } = renderStars();

    // Driven with `fireEvent` rather than `userEvent`: the point is where focus
    // lands when the pressed control unmounts, and that has to be observed
    // across the timers the collapse runs on.
    act(() => {
      star(4).focus();
      fireEvent.click(star(4));
    });
    rerender(
      <RatingStars idPrefix="rating" onRate={vi.fn()} rating={4} title={TITLE} />,
    );

    act(() => {
      vi.advanceTimersByTime(CELEBRATION_MS + COLLAPSE_MS);
    });
    expect(
      screen.getByRole("button", { name: `Change rating for ${TITLE}` }),
    ).toHaveFocus();
  });

  it("skips straight to the chip under prefers-reduced-motion", () => {
    setReducedMotion(true);
    vi.useFakeTimers();
    const { rerender } = renderStars();

    rerender(
      <RatingStars idPrefix="rating" onRate={vi.fn()} rating={3} title={TITLE} />,
    );

    // No timers, no celebration frame: the result of the rating is identical
    // and only the animation is gone.
    expect(vi.getTimerCount()).toBe(0);
    expect(screen.getByText("You rated 3/5")).toBeVisible();
  });

  it("acknowledges a value that was re-confirmed rather than changed", async () => {
    // Pressing the star already on the record commits successfully and changes
    // nothing, so an acknowledgement watching `rating` alone would never fire
    // and the row would sit open after a perfectly good press.
    setReducedMotion(true);
    renderStars({ rating: 4 });

    await userEvent.click(
      screen.getByRole("button", { name: `Change rating for ${TITLE}` }),
    );
    await userEvent.click(star(4));

    await waitFor(() => expect(screen.getByText("You rated 4/5")).toBeVisible());
  });
});

describe("the collapsed chip", () => {
  it("opens collapsed for a movie that already carries a rating", async () => {
    const { container } = renderStars({ rating: 4.5 });

    // Half-star values arrive from the Library's editor and have to read back
    // correctly here, even though this row only sets whole stars.
    expect(screen.getByText("You rated 4.5/5")).toBeVisible();
    expect(screen.queryByRole("button", { name: /stars for/ })).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("reopens pre-filled, with focus on the recorded value", async () => {
    renderStars({ rating: 4 });

    await userEvent.click(
      screen.getByRole("button", { name: `Change rating for ${TITLE}` }),
    );

    await waitFor(() => expect(star(4)).toHaveFocus());
    expect(star(4)).toHaveAttribute("aria-pressed", "true");
    expect(star(5)).not.toHaveClass("is-filled");
  });

  it("keeps the honest sentence about what a star records", () => {
    renderStars({ rating: 4 });

    const panel = screen.getByRole("group", { name: "Your rating" });
    expect(within(panel).getByText(/not a graded training signal/)).toBeVisible();
  });
});
