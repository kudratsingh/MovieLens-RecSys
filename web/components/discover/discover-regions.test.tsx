import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { DiscoverExperience } from "@/components/discover/discover-experience";
import { WatchHistory } from "@/components/discover/watch-history";
import type { HistoryResponse, RecommendationResponse } from "@/lib/api";
import {
  discoverHistory,
  emptyHistory,
  learnedRecommendations,
} from "@/lib/fixtures/discover-fixtures";
import {
  emptyState,
  failureState,
  readyState,
  type ResourceState,
} from "@/lib/resources/state";

const REQUEST_ID = "9b2f6d55-8b23-4a53-9d6e-6a2c1cf00d21";

function renderRoute(
  recommendations: ResourceState<RecommendationResponse>,
  history: ResourceState<HistoryResponse>,
) {
  return render(
    <main>
      <DiscoverExperience
        browseHref="/browse?user=900000101"
        initialRecommendations={recommendations}
        limit={10}
        movieHrefBase="/movies"
        personaName="Action Fan"
        quickPicksHref="/quick-picks?user=900000101"
        userId={900000101}
      />
      <WatchHistory
        browseHref="/browse?user=900000101"
        movieHref={(movieId) => `/movies/${movieId}?user=900000101`}
        personaName="Action Fan"
        state={history}
      />
    </main>,
  );
}

describe("Discover regions fail independently", () => {
  it("keeps the movie decision usable when watch history fails", async () => {
    const { container } = renderRoute(
      readyState("recommendations", learnedRecommendations, REQUEST_ID),
      failureState({
        status: "upstream-error",
        resource: "history",
        reason: "server",
        requestId: REQUEST_ID,
      }),
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Why this?" })).toBeVisible();
    expect(
      screen.getByRole("alert", { name: /Watch history could not be loaded/ }),
    ).toHaveTextContent("Watch history could not be loaded");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps watch history readable when recommendations fail", async () => {
    const { container } = renderRoute(
      failureState({
        status: "upstream-error",
        resource: "recommendations",
        reason: "timeout",
        requestId: REQUEST_ID,
      }),
      readyState("history", discoverHistory, REQUEST_ID),
    );

    expect(
      screen.getByRole("alert", { name: /Recommendations could not be loaded/ }),
    ).toBeVisible();
    // The row prints the display title and moves the year onto the metadata
    // line, and it is a link: history was four dead lines before the read model
    // carried a poster and a structured year.
    const heat = screen.getByRole("link", { name: /Heat/ });
    expect(heat).toHaveAttribute("href", "/movies/6?user=900000101");
    expect(heat).toHaveTextContent("Heat");
    expect(heat).toHaveTextContent("1995 · Action · Crime · Thriller · rated 4.5");
    expect(screen.queryByText("Heat (1995)")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /has watched/ })).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("gives a history row without artwork the shared fallback mark", () => {
    renderRoute(
      readyState("recommendations", learnedRecommendations, REQUEST_ID),
      readyState("history", discoverHistory, REQUEST_ID),
    );

    // `Saving Private Ryan` is the fixture row with `poster_url: null`. The
    // mark is decorative — the row's own title is the accessible name — so it
    // is found structurally rather than by an announcement no reader wants
    // four times down a list.
    const ryan = screen.getByRole("link", { name: /Saving Private Ryan/ });
    expect(ryan.querySelector("[data-testid='poster-fallback']")).not.toBeNull();
    expect(ryan.querySelector("img")).toBeNull();

    const heat = screen.getByRole("link", { name: /Heat/ });
    expect(heat.querySelector("[data-testid='poster-fallback']")).toBeNull();
    expect(heat.querySelector("img")).not.toBeNull();
  });

  it("treats an empty history as a state rather than an error", async () => {
    const { container } = renderRoute(
      readyState("recommendations", learnedRecommendations, REQUEST_ID),
      emptyState("history", emptyHistory, REQUEST_ID),
    );

    expect(screen.getByText("No watch history yet")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});
