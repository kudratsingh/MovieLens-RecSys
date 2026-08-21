import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MovieDetailView } from "@/components/movie/movie-detail-view";
import type { CatalogItem, MovieState } from "@/lib/api";

const USER_ID = 900000101;

const movieState: MovieState = {
  tenant_id: "demo",
  user_id: USER_ID,
  movie_id: 101,
  rating: null,
  rating_updated_at: null,
  watched_at: null,
  watchlisted_at: null,
  dismissed_at: null,
  revision: 4,
  updated_at: "2026-08-20T12:00:00Z",
};

function item(overrides: Partial<CatalogItem> = {}): CatalogItem {
  return {
    movie_id: 101,
    title: "The Handmaiden",
    genres: ["Thriller", "Drama"],
    tmdb_id: "290098",
    release_year: 2016,
    poster_url: "/posters/handmaiden.svg",
    overview: "A con artist enters a secluded estate.",
    metadata_source: "reviewed-fixture",
    source_status: "complete",
    state: null,
    interaction_count: 42,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function committed(overrides: Partial<MovieState>) {
  return jsonResponse({
    request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
    replayed: false,
    outcome: "changed",
    state: { ...movieState, ...overrides },
  });
}

/** CSRF is answered immediately; mutations come from the supplied queue. */
function stubFetch(...queued: (Response | Promise<Response>)[]) {
  const calls: string[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/auth/csrf")) return jsonResponse({ csrfToken: "token" });
    calls.push(url);
    return queued.shift() ?? jsonResponse({ detail: "unexpected" }, 500);
  });
  globalThis.fetch = impl as unknown as typeof fetch;
  return { calls };
}

function renderDetail(overrides: Partial<CatalogItem> = {}) {
  return render(
    <MovieDetailView
      backHref="/browse?q=burning"
      item={item(overrides)}
      requestId="0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001"
      userId={USER_ID}
    />,
  );
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("movie detail content", () => {
  it("leads with the movie and names its metadata provenance", async () => {
    const { container } = renderDetail();

    expect(
      screen.getByRole("heading", { level: 1, name: "The Handmaiden" }),
    ).toBeVisible();
    expect(screen.getByText("Reviewed snapshot · Complete details")).toBeVisible();
    expect(screen.getByText("2016 · Thriller · Drama")).toBeVisible();
    expect(screen.getByRole("link", { name: /Back to Browse/ })).toHaveAttribute(
      "href",
      "/browse?q=burning",
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("names a partial record and substitutes a source-aware synopsis", () => {
    renderDetail({
      metadata_source: "tmdb-snapshot",
      source_status: "partial",
      overview: null,
      poster_url: null,
    });

    expect(screen.getByText("TMDB snapshot · Partial details")).toBeVisible();
    expect(
      screen.getByText(/not part of the reviewed metadata snapshot/),
    ).toBeVisible();
    expect(screen.getByTestId("poster-fallback")).toBeVisible();
  });

  it("degrades an unavailable record to what MovieLens does hold", async () => {
    const { container } = renderDetail({
      metadata_source: "movielens",
      source_status: "unavailable",
      overview: null,
      poster_url: null,
      release_year: null,
      genres: [],
      tmdb_id: null,
    });

    expect(screen.getByText("MovieLens catalog · Details unavailable")).toBeVisible();
    expect(screen.getByText("Year unavailable · Genre unavailable")).toBeVisible();
    expect(screen.getByText(/There is no synopsis to show/)).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows no explanation when the resource carries none", async () => {
    renderDetail();
    expect(screen.queryByLabelText("Why this movie")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Record details" }));
    expect(screen.getByText("TMDB 290098")).toBeVisible();
    expect(screen.getByText(/carries no ranking score/)).toBeVisible();
    // Nothing anywhere presents a score as a percentage match.
    expect(screen.queryByText(/% match/)).not.toBeInTheDocument();
  });

  it("renders a structured explanation only when one is supplied", () => {
    render(
      <MovieDetailView
        backHref="/browse"
        explanation={{
          reason: "Similar to movies in this persona's watched history.",
          servingPolicy: "item-item-lightgbm",
          modelVersion: "lgbm-ranker-2026.08",
        }}
        item={item()}
        requestId="rid"
        userId={USER_ID}
      />,
    );

    const explanation = screen.getByLabelText("Why this movie");
    expect(explanation).toHaveTextContent(
      "Similar to movies in this persona's watched history.",
    );
    expect(explanation).toHaveTextContent("item-item-lightgbm · lgbm-ranker-2026.08");
  });
});

describe("canonical state controls", () => {
  it("reconciles from the committed response and reuses its revision", async () => {
    const { calls } = stubFetch(
      committed({ watchlisted_at: "2026-08-21T10:00:00Z", revision: 5 }),
      committed({ watchlisted_at: null, revision: 6 }),
    );
    renderDetail({ state: movieState });

    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    const saved = await screen.findByRole("button", { name: "In watchlist" });
    expect(saved).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Watchlist is organisational and changes no recommendation input",
    );
    expect(calls[0]).toContain("expected_revision=4");

    await userEvent.click(saved);
    await waitFor(() => expect(calls).toHaveLength(2));
    // The second write sends the revision the API committed, not a guess.
    expect(calls[1]).toContain("expected_revision=5");
  });

  it("rolls back and returns focus to the control when a write fails", async () => {
    let settle!: (value: Response) => void;
    stubFetch(new Promise<Response>((resolve) => (settle = resolve)));
    renderDetail({ state: movieState });

    const watchlist = screen.getByRole("button", { name: "Watchlist" });
    await userEvent.click(watchlist);
    expect(screen.getByRole("button", { name: "In watchlist" })).toBeVisible();

    // Focus moves away while the write is in flight, as it would if the viewer
    // kept reading; the failure has to bring it back.
    screen.getByRole("link", { name: /Back to Browse/ }).focus();
    settle(jsonResponse({ detail: "upstream" }, 502));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The recommendation API returned an error. Nothing was saved for The Handmaiden.",
      ),
    );
    expect(screen.getByRole("button", { name: "Watchlist" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(document.activeElement).toHaveAccessibleName("Watchlist");
  });

  it("reports a conflicting revision as a conflict, not a generic failure", async () => {
    stubFetch(jsonResponse({ detail: "state revision does not match" }, 409));
    renderDetail({ state: movieState });

    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "changed somewhere else before this saved",
    );
    expect(screen.getByRole("button", { name: "Watchlist" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("reveals rating only after watched and keeps the rating claim narrow", async () => {
    stubFetch(committed({ watched_at: "2026-08-21T10:00:00Z", revision: 5 }));
    const { container } = renderDetail({ state: movieState });

    expect(screen.queryByRole("group", { name: "Your rating" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark watched" }));

    expect(await screen.findByRole("group", { name: "Your rating" })).toBeVisible();
    expect(
      screen.getByText(/Star magnitude is display feedback today/),
    ).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("confirms before removing a watched interaction", async () => {
    const { calls } = stubFetch(committed({ watched_at: null, revision: 5 }));
    renderDetail({
      state: { ...movieState, watched_at: "2026-08-01T10:00:00Z", revision: 4 },
    });

    await userEvent.click(screen.getByRole("button", { name: "Watched · remove" }));
    expect(calls).toHaveLength(0);
    expect(
      screen.getByText(/also removes the watched interaction/),
    ).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Confirm removal" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toContain("/movies/101/watched?expected_revision=4");
  });

  it("keeps dismissal undoable and free of any learned-negative claim", async () => {
    stubFetch(committed({ dismissed_at: "2026-08-21T10:00:00Z", revision: 5 }));
    renderDetail({ state: movieState });

    await userEvent.click(screen.getByRole("button", { name: "Not for me" }));

    expect(await screen.findByRole("button", { name: "Undo dismissal" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(
      "excluded from recommendations and can be undone",
    );
  });
});
