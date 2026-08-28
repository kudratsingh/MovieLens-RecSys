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
import { recordCommittedState } from "@/lib/movie-state/committed-store";
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
        quickPicksHref="/quick-picks?user=900000101"
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
    const { container } = renderDiscover(
      readyState("recommendations", posterFailureRecommendations, REQUEST_ID),
    );

    // The poster carries an empty alt — it sits inside a link that already
    // names the movie — so it is reached as an element rather than by name.
    const poster = container.querySelector(".featured-poster img");
    expect(poster).not.toBeNull();
    fireEvent.error(poster as HTMLElement);

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
    // The committed action is still reported, and the queue still advanced:
    // the advance follows the commit, not the refetch, so a dead refresh
    // cannot strand the viewer on a movie they have already decided about.
    expect(screen.getByText(/saved to watchlist/)).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 1, name: "In the Mood for Love" }),
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

  it("commits the first press of a control on a title with existing state", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) {
        // A recommendation carries no state, so the first write can only
        // assert revision 0 — which is stale for any title that has ever been
        // written and reverted.
        return url.includes("expected_revision=0")
          ? Response.json({ detail: "state revision 5 is stale" }, { status: 409 })
          : Response.json(committed({ revision: 6 }));
      }
      if (url.includes("/movies/101")) return Response.json(detailWithWatchlist);
      return Response.json({
        ...learnedRecommendations,
        items: learnedRecommendations.items.slice(1),
      });
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    // One press, one committed decision. The write path re-read the canonical
    // record and replayed the same intent against it.
    await screen.findByText(/saved to watchlist/);
    expect(screen.queryByText(/changed somewhere else/)).not.toBeInTheDocument();
    expect(calls.filter((url) => url.includes("/watchlist"))).toEqual([
      "/api/users/900000101/movies/101/watchlist?expected_revision=0",
      "/api/users/900000101/movies/101/watchlist?expected_revision=5",
    ]);
  });

  it("reports a conflict it could not resolve, and corrects the control", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) {
        // Quoted from `IdempotencyConflictError` in `src/serving/feedback.py`:
        // the client tells a race apart from a refused transition by this
        // body, so an invented sentence would not exercise the split.
        return Response.json(
          { detail: "idempotency key was already used for another mutation" },
          { status: 409 },
        );
      }
      if (url.includes("/movies/101")) return Response.json(detailWithWatchlist);
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/changed somewhere else before this saved/);
    // The canonical record came back on the result, so the control shows the
    // truth instead of an inviting button that would fail again.
    const corrected = await featuredRegion().findByRole("button", {
      name: "In watchlist",
    });
    expect(corrected).toHaveAttribute("aria-pressed", "true");
    // Exactly one replay: two attempts, never a loop.
    expect(calls.filter((url) => url.includes("/watchlist"))).toHaveLength(2);
  });

  it("states the rule when the API refuses the transition, and offers no retry", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    // Quoted from `InvalidStateTransitionError` in `src/serving/feedback.py`.
    // It shares the 409 with a stale revision, and this route used to report it
    // as "changed somewhere else before this saved" — untrue, since nothing
    // changed anywhere — and spend a re-read plus a replay proving it.
    const rule = "a watched movie cannot be added to the watchlist";
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) {
        return Response.json({ detail: rule }, { status: 409 });
      }
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(new RegExp(rule));
    expect(screen.queryByText(/changed somewhere else/)).not.toBeInTheDocument();
    // One attempt, no replay, and no canonical read behind it.
    expect(calls.filter((url) => url.includes("/watchlist"))).toHaveLength(1);
    expect(calls.some((url) => /\/movies\/\d+$/.test(url))).toBe(false);
    // The queue did not move and the control fell back to its committed value.
    const control = featuredRegion().getByRole("button", { name: "Watchlist" });
    expect(control).toHaveAttribute("aria-pressed", "false");
  });

  it("re-pressing a failed control replays one decision under one key", async () => {
    const user = userEvent.setup();
    const keys: (string | null)[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) {
        keys.push(new Headers(init?.headers).get("Idempotency-Key"));
        return Response.json({ detail: "the API is down" }, { status: 502 });
      }
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    const control = () => featuredRegion().getByRole("button", { name: "Watchlist" });
    await user.click(control());
    await screen.findByText(/was not saved/);
    await user.click(control());
    await waitFor(() => expect(keys).toHaveLength(2));

    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[1]).toBe(keys[0]);
  });

  it("does not claim a refresh the viewer cannot see", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) return Response.json(committed());
      // Watchlist is organizational, so the ranked set legitimately comes back
      // exactly as it was.
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/The ranked list is unchanged/);
    expect(screen.queryByText(/Recommendations refreshed/)).not.toBeInTheDocument();
    expect(screen.getByText(/saved to watchlist/)).toBeVisible();
  });

  it("keeps a second press out of the way while the first is in flight", async () => {
    const user = userEvent.setup();
    let releaseWrite = () => {};
    const held = new Promise<void>((resolve) => {
      releaseWrite = resolve;
    });
    const writes: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist") || url.includes("/dismissal")) {
        writes.push(url);
        await held;
        return Response.json(committed());
      }
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));
    await screen.findByText(/Saving The Handmaiden/);
    await user.click(featuredRegion().getByRole("button", { name: "Not for me" }));

    // Two writes in flight would both assert the revision they started from,
    // and the later response would land on top of the earlier one.
    expect(writes).toHaveLength(1);
    releaseWrite();
    await screen.findByText(/saved to watchlist/);
  });

  it("lands focus on the movie that arrived, never on the status line", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) return Response.json(committed());
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/saved to watchlist/);
    // The same control on the card that arrived, so a run of decisions stays
    // under one finger rather than restarting at the top of the document.
    await waitFor(() =>
      expect(document.getElementById("featured-102-watchlist")).toHaveFocus(),
    );
  });

  it("offers the rating prompt for the watched action, not for a watched state", async () => {
    const user = userEvent.setup();
    // The title is already watched, so every committed response carries a
    // `watched_at` — including this watchlist press.
    recordCommittedState(window.sessionStorage, 900000101, {
      ...committed().state,
      revision: 5,
      watched_at: "2026-08-20T09:00:00Z",
      watchlisted_at: null,
    });
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) {
        return Response.json(
          committed({ revision: 6, watched_at: "2026-08-20T09:00:00Z" }),
        );
      }
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(await featuredRegion().findByRole("button", { name: "Watchlist" }));

    await screen.findByText(/saved to watchlist/);
    expect(
      screen.queryByRole("region", { name: "Rate The Handmaiden" }),
    ).not.toBeInTheDocument();
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

describe("Quick Picks is reachable without claiming a fourth navigation slot", () => {
  it("offers a labelled entry beside the ranked set", () => {
    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));

    const entry = screen.getByRole("link", { name: /Quick picks/ });
    expect(entry).toHaveAttribute("href", "/quick-picks?user=900000101");
    expect(entry).toHaveTextContent("Rate a few in Quick picks");
  });

  it("still offers it when there is no ranked set to read", () => {
    renderDiscover(
      emptyState("recommendations", { ...learnedRecommendations, items: [] }, REQUEST_ID),
    );

    expect(screen.getByRole("link", { name: /Quick picks/ })).toHaveAttribute(
      "href",
      "/quick-picks?user=900000101",
    );
    expect(screen.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
  });
});

describe("cards start from the state other routes already committed", () => {
  it("shows a watchlist set elsewhere and asserts the revision it was given", async () => {
    const user = userEvent.setup();
    recordCommittedState(window.sessionStorage, 900000101, {
      ...committed().state,
      revision: 5,
      watchlisted_at: "2026-08-21T09:00:00Z",
    });

    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/watchlist")) return Response.json(committed({ revision: 6 }));
      return Response.json(learnedRecommendations);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));

    // The relay is read after hydration, so the card corrects itself rather
    // than inviting a save that would answer 409.
    const saved = await featuredRegion().findByRole("button", { name: "In watchlist" });
    expect(saved).toHaveAttribute("aria-pressed", "true");

    await user.click(saved);
    await waitFor(() =>
      expect(calls.some((url) => url.includes("expected_revision=5"))).toBe(true),
    );
  });
});

describe("the featured slot is a queue position, not a projection", () => {
  /** Answers the CSRF read, one mutation, and every refetch from one list. */
  function stubStack(options: {
    mutation?: (url: string) => Response;
    ranked?: RecommendationResponse | (() => RecommendationResponse);
  } = {}) {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      calls.push(url);
      if (url.includes("/api/auth/csrf")) return Response.json({ csrfToken: "token" });
      if (url.includes("/movies/")) {
        return options.mutation?.(url) ?? Response.json(committed());
      }
      const ranked = options.ranked ?? learnedRecommendations;
      return Response.json(typeof ranked === "function" ? ranked() : ranked);
    });
    vi.stubGlobal("fetch", fetchImpl);
    return calls;
  }

  const decisions = [
    { control: "Watchlist", label: "watchlist" },
    { control: "Mark watched", label: "watched" },
    { control: "Not for me", label: "dismissal" },
  ] as const;

  it.each(decisions)(
    "advances the featured movie after a committed $label decision",
    async ({ control }) => {
      const user = userEvent.setup();
      stubStack();

      renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
      await user.click(featuredRegion().getByRole("button", { name: control }));

      // Watchlist is the one that used to change nothing at all: it excludes
      // no titles, so the backend returned the same first item and the movie
      // sat there. On Discover, `Watchlist` means "save it, next".
      expect(
        await screen.findByRole("heading", { level: 1, name: "In the Mood for Love" }),
      ).toBeVisible();
    },
  );

  it("stays on the movie when the commit fails", async () => {
    const user = userEvent.setup();
    stubStack({
      mutation: () => Response.json({ detail: "the API is down" }, { status: 502 }),
    });

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByText(/was not saved/);
    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
  });

  it("never offers a decided title again, even when the API keeps returning it", async () => {
    const user = userEvent.setup();
    // The identical ranked set comes back, which is exactly what a watchlist
    // press produces: it excludes nothing server-side.
    stubStack();

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByRole("heading", { level: 1, name: "In the Mood for Love" });
    await waitFor(() =>
      expect(screen.queryByText("The Handmaiden")).not.toBeInTheDocument(),
    );
  });

  it("starts the rail at the movie after the featured one", async () => {
    const user = userEvent.setup();
    stubStack();

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    const rail = () => within(screen.getByRole("region", { name: /ranked set/ }));
    // No title is both the decision and part of what is still ahead of it.
    expect(rail().queryByText("The Handmaiden")).not.toBeInTheDocument();
    expect(rail().getByText("In the Mood for Love")).toBeVisible();

    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));
    await screen.findByRole("heading", { level: 1, name: "In the Mood for Love" });
    await waitFor(() =>
      expect(rail().queryByText("In the Mood for Love")).not.toBeInTheDocument(),
    );
    expect(rail().getByText("Memories of Murder")).toBeVisible();
  });

  it("announces what was recorded and what is now featured", async () => {
    const user = userEvent.setup();
    stubStack();

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Not for me" }));

    // One sentence for a reader who cannot see the card move: the decision,
    // then the movie it moved to.
    await screen.findByText(
      /The Handmaiden will be excluded from recommendations\. Next: In the Mood for Love\./,
    );
  });

  it("offers Undo for a watchlist decision and puts both halves back", async () => {
    const user = userEvent.setup();
    const calls = stubStack();

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));
    await screen.findByRole("heading", { level: 1, name: "In the Mood for Love" });

    await user.click(await screen.findByRole("button", { name: /Undo saving/ }));

    // The reversal asserts the revision the commit returned…
    await waitFor(() =>
      expect(
        calls.some((url) => url.includes("/movies/101/watchlist?expected_revision=3")),
      ).toBe(true),
    );
    // …and the cursor comes back with it, because undoing the write while
    // leaving the viewer past the card is only half an undo.
    expect(
      await screen.findByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
  });

  it("offers no bare Undo after a watched decision", async () => {
    const user = userEvent.setup();
    stubStack({
      mutation: () =>
        Response.json(committed({ watched_at: "2026-08-21T09:00:00Z", watchlisted_at: null })),
    });

    renderDiscover(readyState("recommendations", learnedRecommendations, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Mark watched" }));

    // Reversing watched history is a confirmed destructive edit; the honest
    // affordances here are the rating prompt and a named way to the Library.
    const panel = within(await screen.findByRole("region", { name: /^Rate / }));
    expect(panel.getByRole("link", { name: "Manage in Library" })).toHaveAttribute(
      "href",
      "/library?userId=900000101",
    );
    expect(screen.queryByRole("button", { name: /^Undo/ })).not.toBeInTheDocument();
  });

  it("swaps instantly under reduced motion and says exactly the same thing", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    stubStack();

    const { container } = renderDiscover(
      readyState("recommendations", learnedRecommendations, REQUEST_ID),
    );
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    await screen.findByRole("heading", { level: 1, name: "In the Mood for Love" });
    // The announcement is the authoritative channel and is unchanged; the
    // arrival simply carries no direction to animate along.
    expect(screen.getByText(/Next: In the Mood for Love/)).toBeVisible();
    expect(container.querySelector("[data-enter-from]")).toBeNull();
  });

  it("marks the arrival with the direction the decision travelled", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    stubStack();

    const { container } = renderDiscover(
      readyState("recommendations", learnedRecommendations, REQUEST_ID),
    );
    await user.click(featuredRegion().getByRole("button", { name: "Not for me" }));

    await screen.findByRole("heading", { level: 1, name: "In the Mood for Love" });
    // Left for a dismissal, the same direction the Quick Picks swipe uses.
    expect(container.querySelector(".featured-movie")).toHaveAttribute(
      "data-enter-from",
      "left",
    );
  });

  it("names both ways forward once the queue is spent", async () => {
    const user = userEvent.setup();
    const single = { ...learnedRecommendations, items: learnedRecommendations.items.slice(0, 1) };
    stubStack({ ranked: single });

    renderDiscover(readyState("recommendations", single, REQUEST_ID));
    await user.click(featuredRegion().getByRole("button", { name: "Watchlist" }));

    expect(await screen.findByText(/through this ranked set/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Browse the catalog" })).toBeVisible();
    expect(screen.getByRole("link", { name: /Quick picks/ })).toBeVisible();
  });

  it("extends in the background without disturbing the movie being read", async () => {
    const short = { ...learnedRecommendations, items: learnedRecommendations.items.slice(0, 3) };
    stubStack({ ranked: learnedRecommendations });

    renderDiscover(readyState("recommendations", short, REQUEST_ID));

    // Three titles is inside the extension trigger, so the queue tops itself
    // up; the featured movie is untouched by it.
    expect(await screen.findByText("Portrait of a Lady on Fire")).toBeVisible();
    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
  });
});
