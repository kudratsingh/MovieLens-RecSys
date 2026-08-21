import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { QuickPicksDeck } from "@/components/quick-picks/quick-picks-deck";
import { createFixtureQuickPickTransport } from "@/lib/quick-picks/fixture-transport";
import {
  fixtureMovieTitle,
  fixtureQuickPickEvidence,
  fixtureQuickPickResponse,
} from "@/lib/quick-picks/fixtures";
import { SWIPE_DISTANCE_PX } from "@/lib/quick-picks/machine";
import type { QuickPickQueuePayload } from "@/lib/quick-picks/transport";
import { failureState, readyState } from "@/lib/resources/state";

function payload(
  options: { learned?: boolean; movieIds?: readonly number[] } = {},
): QuickPickQueuePayload {
  return {
    queue: readyState(
      "recommendations",
      fixtureQuickPickResponse(options),
      "req-quick-picks",
      "recorded-contract-fixture",
    ),
    evidence: fixtureQuickPickEvidence(options.learned ?? false),
  };
}

function renderDeck(
  options: {
    initial?: QuickPickQueuePayload;
    failCommits?: boolean;
  } = {},
) {
  const initial = options.initial ?? payload();
  const transport = createFixtureQuickPickTransport({
    failCommits: options.failCommits,
    initial,
    resolveSeedTitle: fixtureMovieTitle,
  });
  return render(
    <QuickPicksDeck
      browseHref="/browse?user=900000101"
      initial={initial}
      personaLabel="Action Fan"
      transport={transport}
    />,
  );
}

const dismissButton = () => screen.getByRole("button", { name: /Not for me/ });

/**
 * jsdom has no `PointerEvent`, so Testing Library falls back to a plain `Event`
 * and drops the coordinates a swipe is made of. Extending `MouseEvent` gives the
 * gesture tests real `clientX`/`clientY` without changing what the component
 * listens for.
 */
beforeAll(() => {
  class TestPointerEvent extends MouseEvent {
    readonly isPrimary: boolean;
    readonly pointerId: number;

    constructor(type: string, init: MouseEventInit & { isPrimary?: boolean; pointerId?: number } = {}) {
      super(type, init);
      this.isPrimary = init.isPrimary ?? true;
      this.pointerId = init.pointerId ?? 1;
    }
  }
  // Assigned rather than stubbed so per-test `unstubAllGlobals` leaves it be.
  Object.defineProperty(globalThis, "PointerEvent", {
    configurable: true,
    value: TestPointerEvent,
    writable: true,
  });
});

afterEach(() => vi.unstubAllGlobals());

describe("the decision card", () => {
  it("presents one movie with enough context to decide", async () => {
    const { container } = renderDeck();

    expect(screen.getByRole("heading", { level: 1, name: "Perfect Blue" })).toBeVisible();
    expect(screen.getByText(/1997 · Animation · Thriller/)).toBeVisible();
    expect(
      screen.getByText(/A performer loses her footing between image, memory, and reality/),
    ).toBeVisible();
    expect(screen.getByText("Popular with viewers in this tenant")).toBeVisible();
    expect(screen.getByText("Exploring as Action Fan")).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("gives every action a visible button and a keyboard hint", () => {
    renderDeck();

    for (const name of [/Not for me/, /Watchlist/, /Watched/]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
    // The shared star control: the same accessible name as detail and Library.
    expect(screen.getByRole("button", { name: "4 stars for Perfect Blue" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Exit to Browse" })).toHaveAttribute(
      "href",
      "/browse?user=900000101",
    );
  });

  it("never renders a rank score", () => {
    const { container } = renderDeck({ initial: payload({ learned: true }) });

    // 0.91 is the recorded top score; a match percentage would be its shadow.
    expect(container.textContent).not.toContain("0.91");
    expect(container.textContent).not.toMatch(/\d+% match/i);
  });
});

describe("decisions reach the same canonical outcome from every input", () => {
  it("advances the queue after a button decision and announces it once", async () => {
    const user = userEvent.setup();
    renderDeck();

    await user.click(dismissButton());

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("In the Mood for Love"),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Perfect Blue: not for me saved.");
  });

  it("advances the queue from the keyboard", async () => {
    const user = userEvent.setup();
    renderDeck();

    await user.keyboard("j");

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("In the Mood for Love"),
    );
  });

  it("advances the queue from a pointer swipe", async () => {
    const { container } = renderDeck();
    const poster = container.querySelector(".quick-pick-poster");
    if (!poster) throw new Error("The swipe surface is missing");

    fireEvent.pointerDown(poster, { clientX: 200, clientY: 200, isPrimary: true, pointerId: 1 });
    fireEvent.pointerMove(poster, { clientX: 200 - SWIPE_DISTANCE_PX, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(poster, { clientX: 200 - SWIPE_DISTANCE_PX, clientY: 200, pointerId: 1 });

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("In the Mood for Love"),
    );
  });
});

describe("progress toward learned serving", () => {
  it("uses fallback copy and counts only committed watched signals", async () => {
    const user = userEvent.setup();
    renderDeck();

    expect(screen.getByRole("heading", { name: "Popular while we learn" })).toBeVisible();
    expect(screen.getByText("2 of 5 positive watched signals")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /^Watchlist/ }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("watchlist saved"),
    );
    expect(screen.getByText("2 of 5 positive watched signals")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /^Watched/ }));
    await waitFor(() =>
      expect(screen.getByText("3 of 5 positive watched signals")).toBeVisible(),
    );
  });

  it("treats a star as one watched decision rather than a second step", async () => {
    const user = userEvent.setup();
    renderDeck();

    await user.click(screen.getByRole("button", { name: "4 stars for Perfect Blue" }));

    await waitFor(() =>
      expect(screen.getByText("3 of 5 positive watched signals")).toBeVisible(),
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("In the Mood for Love");
    expect(screen.getByRole("status")).toHaveTextContent("Perfect Blue: watched saved.");
  });

  it("claims learned serving only when the returned policy reports it", () => {
    renderDeck({ initial: payload({ learned: true }) });

    expect(
      screen.getByRole("heading", { name: "Picked from your watched history" }),
    ).toBeVisible();
    expect(screen.getByText("The last response reported learned serving.")).toBeVisible();
  });

  it("shows the exclusion count and filter policy the response reported", () => {
    renderDeck();
    expect(
      screen.getByText(/3 titles excluded · watched-and-dismissed-excluded-v1/),
    ).toBeVisible();
  });
});

describe("undo", () => {
  it("appears after a dismissal and restores the title", async () => {
    const user = userEvent.setup();
    renderDeck();

    expect(screen.queryByRole("button", { name: /^Undo/ })).not.toBeInTheDocument();
    await user.click(dismissButton());

    const undo = await screen.findByRole("button", {
      name: /Undo not for me for Perfect Blue/,
    });
    await user.click(undo);

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Perfect Blue"),
    );
    expect(screen.getByRole("status")).toHaveTextContent("back in the queue");
  });

  it("is not offered for a watchlist save", async () => {
    const user = userEvent.setup();
    renderDeck();

    await user.click(screen.getByRole("button", { name: /^Watchlist/ }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("watchlist saved"),
    );
    expect(screen.queryByRole("button", { name: /^Undo/ })).not.toBeInTheDocument();
  });
});

describe("failure handling", () => {
  it("keeps the card, restores the controls, and returns focus to the button", async () => {
    const user = userEvent.setup();
    const { container } = renderDeck({ failCommits: true });

    await user.click(dismissButton());

    await waitFor(() =>
      expect(
        screen.getByText("The recommendation API could not save that decision."),
      ).toBeVisible(),
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Perfect Blue");
    expect(dismissButton()).toBeEnabled();
    expect(document.activeElement).toBe(dismissButton());
    expect(screen.getByRole("status")).toHaveTextContent("The card is unchanged.");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("offers a reauthentication path and a retry when the queue itself failed", async () => {
    const { container } = renderDeck({
      initial: {
        queue: failureState({
          status: "upstream-error",
          resource: "recommendations",
          reason: "timeout",
          requestId: "req-failed",
        }),
        evidence: {},
      },
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The recommendation API did not answer in time.",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("queue exhaustion", () => {
  it("offers a restart and a Browse path once the last card is decided", async () => {
    const user = userEvent.setup();
    const { container } = renderDeck({ initial: payload({ movieIds: [105] }) });

    await user.click(dismissButton());

    expect(
      await screen.findByRole("heading", { name: "That is every pick we have for now" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Get more picks" })).toBeEnabled();
    expect(screen.getAllByRole("link", { name: /Browse/ })[0]).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("why this?", () => {
  it("names the seed title the audit recorded", async () => {
    const user = userEvent.setup();
    renderDeck({ initial: payload({ learned: true }) });

    await user.click(screen.getByRole("button", { name: "Why this?" }));

    expect(
      await screen.findByText(
        "Retrieved as similar to Memories of Murder, which this persona has watched.",
      ),
    ).toBeVisible();
  });

  it("falls back to the candidate source when there is no seed", async () => {
    const user = userEvent.setup();
    renderDeck();

    await user.click(screen.getByRole("button", { name: "Why this?" }));

    expect(
      await screen.findByText("Selected by tenant popularity while the model learns."),
    ).toBeVisible();
  });
});

describe("reduced motion", () => {
  it("drops the fling but keeps the swipe working", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    const { container } = renderDeck();
    const poster = container.querySelector(".quick-pick-poster");
    if (!poster) throw new Error("The swipe surface is missing");

    await waitFor(() => expect(poster.getAttribute("data-motion")).toBe("none"));

    fireEvent.pointerDown(poster, { clientX: 200, clientY: 200, isPrimary: true, pointerId: 1 });
    fireEvent.pointerMove(poster, { clientX: 120, clientY: 200, pointerId: 1 });
    expect(poster.getAttribute("style")).toBeNull();
    fireEvent.pointerUp(poster, { clientX: 120, clientY: 200, pointerId: 1 });

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("In the Mood for Love"),
    );
  });
});
