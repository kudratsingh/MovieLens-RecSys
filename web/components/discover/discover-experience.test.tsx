import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoverExperience } from "@/components/discover/discover-experience";
import type {
  FeedbackMutationResponse,
  MovieDetailResponse,
  RecommendationResponse,
} from "@/lib/api";
import {
  fallbackRecommendations,
  learnedRecommendations,
  posterFailureRecommendations,
} from "@/lib/fixtures/discover-fixtures";
import {
  emptyState,
  failureState,
  loadingState,
  readyState,
  type ResourceState,
} from "@/lib/resources/state";

const REQUEST_ID = "0e4a5f77-2d3c-4a55-9f6b-9d2c7a4f1a20";

const committed = (
  overrides: Partial<FeedbackMutationResponse["state"]> = {},
): FeedbackMutationResponse => ({
  outcome: "changed",
  replayed: false,
  request_id: "1c0e5b1a-6f10-4b2a-9c3d-9a1d0f9c2a31",
  state: {
    movie_id: 101,
    user_id: 900000101,
    tenant_id: "demo",
    revision: 3,
    updated_at: "2026-08-21T09:00:00Z",
    rating: null,
    rating_updated_at: null,
    watched_at: null,
    watchlisted_at: "2026-08-21T09:00:00Z",
    dismissed_at: null,
    ...overrides,
  },
});

/** What the movie-detail read answers when a conflict is being resolved. */
const detailWithWatchlist: MovieDetailResponse = {
  tenant_id: "demo",
  user_id: 900000101,
  item: {
    movie_id: 101,
    title: "The Handmaiden (2016)",
    genres: ["Thriller", "Drama"],
    interaction_count: 812,
    metadata_source: "reviewed-fixture",
    source_status: "complete",
    release_year: 2016,
    overview: null,
    poster_url: null,
    tmdb_id: null,
    state: { ...committed().state, revision: 5 },
  },
};

function renderDiscover(state: ResourceState<RecommendationResponse>) {
  return render(
    <main>
      <DiscoverExperience
        browseHref="/browse?user=900000101"
        initialRecommendations={state}
        limit={10}
        movieHrefBase="/movies"
        movieHrefQuery="?user=900000101"
        personaName="Action Fan"
        userId={900000101}
      />
    </main>,
  );
}

function featuredRegion() {
  return within(screen.getByRole("region", { name: "The Handmaiden" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  // The committed-state relay is tab-local; keep it from leaking between tests.
  window.sessionStorage.clear();
});

describe("Discover renders the state the response reported", () => {
  it("puts the primary movie first and labels learned serving as learned", async () => {
    const { container } = renderDiscover(
      readyState("recommendations", learnedRecommendations, REQUEST_ID),
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
    expect(featuredRegion().getByText("Ranked by the learned model")).toBeVisible();
    expect(screen.queryByText("Popular while we learn")).not.toBeInTheDocument();
    expect(
      featuredRegion().getByRole("link", { name: /Open movie/ }),
    ).toHaveAttribute("href", "/movies/101?user=900000101");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("calls the popularity fallback what it is and promises nothing further", async () => {
    const { container } = renderDiscover(
      readyState("recommendations", fallbackRecommendations, REQUEST_ID),
    );

    expect(screen.getAllByText("Popular while we learn").length).toBeGreaterThan(0);
    expect(screen.queryByText("Ranked by the learned model")).not.toBeInTheDocument();
    expect(container.textContent?.toLowerCase()).not.toContain("because you liked");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("offers an obvious Browse path beside the ranked rail", () => {
    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));

    expect(
      screen.getByRole("link", { name: "Browse the whole catalog" }),
    ).toHaveAttribute("href", "/browse?user=900000101");
    expect(screen.getByRole("link", { name: "See all" })).toHaveAttribute(
      "href",
      "/browse?user=900000101",
    );
  });

  it("announces loading without claiming a failure", async () => {
    const { container } = renderDiscover(
      loadingState("recommendations") as ResourceState<RecommendationResponse>,
    );

    expect(screen.getByText("Loading movies")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("treats an empty ranked set as a state with a way forward", async () => {
    const { container } = renderDiscover(
      emptyState(
        "recommendations",
        { ...learnedRecommendations, items: [] },
        REQUEST_ID,
      ),
    );

    expect(screen.getByText("No recommendations right now")).toBeVisible();
    expect(screen.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows an upstream failure with its correlation ID and a retry", async () => {
    const { container } = renderDiscover(
      failureState({
        status: "upstream-error",
        resource: "recommendations",
        reason: "timeout",
        requestId: REQUEST_ID,
      }),
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Recommendations could not be loaded");
    expect(alert).toHaveTextContent(REQUEST_ID);
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("leads an expired session back to sign-in", async () => {
    const { container } = renderDiscover(
      failureState({
        status: "auth-expired",
        resource: "recommendations",
        reason: "session-expired",
        requestId: REQUEST_ID,
      }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Your session expired");
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/");
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps the movie identity when its artwork fails to load", () => {
    renderDiscover(
      readyState("recommendations", posterFailureRecommendations, REQUEST_ID),
    );

    fireEvent.error(featuredRegion().getByAltText("Poster for The Handmaiden"));

    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
    expect(featuredRegion().getByTestId("poster-fallback")).toBeVisible();
  });
});

describe("a committed action refreshes recommendations before it says so", () => {
  it("waits for the refetch before claiming the list was refreshed", async () => {
    const user = userEvent.setup();
    let releaseRefresh = () => {};
    const refreshed = new Promise<void>((resolve) => {
      releaseRefresh = resolve;
    });

    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) return Response.json(committed());
      await refreshed;
      return Response.json({
        ...learnedRecommendations,
        items: learnedRecommendations.items.slice(1),
      });
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/Refreshing recommendations/);
    expect(screen.queryByText(/Recommendations refreshed/)).not.toBeInTheDocument();

    releaseRefresh();
    await screen.findByText(/Recommendations refreshed/);
    expect(
      screen.getByRole("heading", { level: 1, name: "In the Mood for Love" }),
    ).toBeVisible();
  });

  it("says so plainly when the action committed but the refresh did not", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) return Response.json(committed());
      return Response.json({ detail: "gateway timeout" }, { status: 504 });
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/could not be refreshed/);
    expect(screen.queryByText(/Recommendations refreshed/)).not.toBeInTheDocument();
    // The committed action is still reported, and the previous list is intact.
    expect(screen.getByText(/saved to watchlist/)).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
  });

  it("rolls the control back and returns focus to it when the mutation fails", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      return Response.json({ detail: "the API is down" }, { status: 502 });
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    const control = featuredRegion().getByRole("button", { name: "Watchlist" });
    await user.click(control);

    await screen.findByText(/was not saved. It was left as it was/);
    expect(featuredRegion().getByRole("button", { name: "Watchlist" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await waitFor(() =>
      expect(featuredRegion().getByRole("button", { name: "Watchlist" })).toHaveFocus(),
    );
    expect(screen.queryByText(/Recommendations refreshed/)).not.toBeInTheDocument();
  });

  it("turns a revision conflict into a correction rather than a dead end", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) {
        // A recommendation carries no state, so the first write can only
        // assert revision 0 — which is stale for an already-saved title.
        return url.includes("expected_revision=0")
          ? Response.json({ detail: "state revision 5 is stale" }, { status: 409 })
          : Response.json(committed({ revision: 6 }));
      }
      if (url.includes("/movies/101")) return Response.json(detailWithWatchlist);
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/changed somewhere else before this saved/);
    // The canonical read corrected the control instead of leaving it wrong.
    const corrected = await featuredRegion().findByRole("button", {
      name: "In watchlist",
    });
    expect(corrected).toHaveAttribute("aria-pressed", "true");

    // The retry now asserts the revision the server issued, and succeeds.
    await user.click(corrected);
    await screen.findByText(/Recommendations refreshed/);
    expect(calls.some((url) => url.includes("expected_revision=5"))).toBe(true);
  });

  it("offers the rating control after a watched action and keeps its claim narrow", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watched")) {
        return Response.json(
          committed({ watched_at: "2026-08-21T09:00:00Z", watchlisted_at: null }),
        );
      }
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Mark watched" }));

    const panel = within(await screen.findByRole("region", { name: "Rate The Handmaiden" }));
    expect(panel.getByText(/Just marked watched/)).toBeVisible();
    expect(
      panel.getByText(/a 1 and a 5 are the same learned signal today/),
    ).toBeVisible();
    expect(
      panel.getByRole("button", { name: "4 stars for The Handmaiden" }),
    ).toBeVisible();
  });
});
