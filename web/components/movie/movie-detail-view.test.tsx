import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MovieDetailView } from "@/components/movie/movie-detail-view";
import type { MovieDetailItem, MovieDetails, MovieState } from "@/lib/api";

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

function item(overrides: Partial<MovieDetailItem> = {}): MovieDetailItem {
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
    // The base case is the record with no enriched block, because that is what
    // most of the catalog holds and what the layout has to survive.
    details: null,
    ...overrides,
  };
}

function details(overrides: Partial<MovieDetails> = {}): MovieDetails {
  return {
    tagline: "Two women. Two cons. One estate.",
    runtime_minutes: 145,
    release_date: "2016-06-01",
    backdrop_url: "/backdrops/handmaiden.svg",
    tmdb_rating: { average: 8.1, count: 4812 },
    directors: ["Park Chan-wook"],
    cast: [
      { name: "Kim Min-hee", character: "Lady Hideko", profile_url: "/profiles/cast-a.svg" },
      { name: "Ha Jung-woo", character: "Count Fujiwara", profile_url: null },
    ],
    trailer: { provider: "youtube", key: "T7kfW4trvUM", name: "Official Trailer" },
    fetched_at: "2026-08-24T09:00:00Z",
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

function renderDetail(overrides: Partial<MovieDetailItem> = {}) {
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

  it("prints the release year once, on the metadata line", () => {
    // MovieLens titles carry their own year. "Toy Story (1995)" over a "1995 ·
    // Children" line is the duplication this route used to render.
    renderDetail({ title: "Toy Story (1995)", release_year: 1995, genres: ["Children"] });

    expect(screen.getByRole("heading", { level: 1, name: "Toy Story" })).toBeVisible();
    expect(screen.getByText("1995 · Children")).toBeVisible();
  });

  it("keeps a parenthetical that is not the structured year", () => {
    renderDetail({ title: "Se7en (1995)", release_year: 1997 });

    expect(screen.getByRole("heading", { level: 1, name: "Se7en (1995)" })).toBeVisible();
  });

  it("marks a missing poster with the initials of the displayed title", () => {
    renderDetail({ title: "Sense and Sensibility (1995)", release_year: 1995, poster_url: null });

    const fallback = screen.getByTestId("poster-fallback");
    expect(fallback).toHaveTextContent("SS");
    expect(fallback).toHaveTextContent("Artwork unavailable");
  });

  it("falls back to the mark when the poster fails to load", () => {
    const { container } = renderDetail();
    const poster = container.querySelector("img");
    if (!poster) throw new Error("The detail poster is missing");

    fireEvent.error(poster);

    expect(screen.getByTestId("poster-fallback")).toBeVisible();
    expect(screen.getByRole("heading", { level: 1, name: "The Handmaiden" })).toBeVisible();
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

describe("enriched TMDB details", () => {
  it("renders the whole record and names where it came from", async () => {
    const { container } = renderDetail({ details: details() });

    expect(screen.getByText("Two women. Two cons. One estate.")).toBeVisible();
    // Runtime joins the line the year already lives on rather than taking a
    // row of its own.
    expect(screen.getByText("2016 · 2h 25m · Thriller · Drama")).toBeVisible();
    expect(screen.getByText("8.1 / 10 · 4,812 ratings")).toBeVisible();
    expect(screen.getByText(/Details from TMDB/)).toBeVisible();
    expect(screen.getByText("Park Chan-wook")).toBeVisible();

    const cast = screen.getByRole("list", { name: "Top-billed cast" });
    expect(cast).toHaveTextContent("Kim Min-hee");
    expect(cast).toHaveTextContent("Lady Hideko");
    // A missing portrait is a monogram, not a silhouette.
    expect(cast).toHaveTextContent("HJ");

    expect(await axe(container)).toHaveNoViolations();
  });

  it("renders exactly today's page when the record carries no details", async () => {
    const { container } = renderDetail({ details: null });

    expect(screen.getByRole("heading", { level: 1, name: "The Handmaiden" })).toBeVisible();
    expect(screen.getByText("2016 · Thriller · Drama")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Play trailer/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Top-billed cast" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Details from TMDB/)).not.toBeInTheDocument();
    // No empty frame stands in for the backdrop that is not there.
    expect(container.querySelector(".movie-detail-backdrop")).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps the enriched fields it has when the optional ones are missing", async () => {
    const { container } = renderDetail({
      details: details({
        tagline: null,
        runtime_minutes: null,
        backdrop_url: null,
        tmdb_rating: null,
        trailer: null,
      }),
    });

    // Each gap is silent rather than labelled: "Runtime unavailable" beside a
    // movie is noise, unlike a missing synopsis, which the route does name.
    expect(screen.getByText("2016 · Thriller · Drama")).toBeVisible();
    expect(screen.queryByText(/ \/ 10 · /)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Play trailer/ })).not.toBeInTheDocument();
    expect(container.querySelector(".movie-detail-backdrop")).toBeNull();
    // What survives still renders, and the attribution goes with it.
    expect(screen.getByRole("list", { name: "Top-billed cast" })).toBeVisible();
    expect(screen.getByText(/Details from TMDB/)).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("loads nothing from YouTube until the trailer is pressed", async () => {
    const { container } = renderDetail({ details: details() });

    // The promise the plate makes, asserted rather than described: no frame,
    // and nothing anywhere on the page pointing at the embed host.
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.innerHTML).not.toContain("youtube-nocookie.com");

    await userEvent.click(screen.getByRole("button", { name: /Play trailer/ }));

    const frame = container.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("src")).toBe(
      "https://www.youtube-nocookie.com/embed/T7kfW4trvUM?autoplay=1&rel=0",
    );
    expect(frame?.getAttribute("title")).toBe("Official Trailer — The Handmaiden");

    // And it can be put away again, with focus back where it started.
    await userEvent.click(screen.getByRole("button", { name: "Close trailer" }));
    expect(container.querySelector("iframe")).toBeNull();
    expect(document.activeElement).toHaveAccessibleName(/Play trailer/);
  });

  it("closes the trailer on Escape", async () => {
    const { container } = renderDetail({ details: details() });

    await userEvent.click(screen.getByRole("button", { name: /Play trailer/ }));
    expect(container.querySelector("iframe")).not.toBeNull();

    await userEvent.keyboard("{Escape}");
    expect(container.querySelector("iframe")).toBeNull();
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

  it("offers rating before the movie is watched and says what a star commits to", async () => {
    const { calls } = stubFetch(
      committed({
        watched_at: "2026-08-21T10:00:00Z",
        rating: 4,
        rating_updated_at: "2026-08-21T10:00:00Z",
        revision: 5,
      }),
    );
    const { container } = renderDetail({ state: movieState });

    // Rating is the shorter path to the same committed state, so it is not
    // hidden behind "Mark watched" — but it must say what it will record.
    const panel = screen.getByRole("group", { name: "Your rating" });
    expect(panel).toBeVisible();
    expect(panel).toHaveTextContent("Rating this records a watch");
    expect(await axe(container)).toHaveNoViolations();

    // The star names are the ones the service-backed journeys address.
    await userEvent.click(
      screen.getByRole("button", { name: "4 stars for The Handmaiden" }),
    );

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toContain("/movies/101/rating?expected_revision=4");
    expect(screen.getByRole("status")).toHaveTextContent("Rating saved.");
    expect(screen.getByRole("status")).toHaveTextContent(
      "star magnitude is not a graded model signal",
    );

    // And the control clears the area it no longer needs: the five stars become
    // the value and one way back into them.
    expect(await screen.findByText("You rated 4/5", {}, { timeout: 3_000 })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "4 stars for The Handmaiden" }),
    ).not.toBeInTheDocument();
  });

  it("narrows the rating note once the movie is already watched", () => {
    renderDetail({
      state: { ...movieState, watched_at: "2026-08-01T10:00:00Z" },
    });

    const panel = screen.getByRole("group", { name: "Your rating" });
    expect(panel).toHaveTextContent(
      "The star value is display feedback today, not a graded training signal.",
    );
    expect(panel).not.toHaveTextContent("records a watch");
  });

  it("keeps removing a rating distinct from removing watched history", async () => {
    const { calls } = stubFetch(committed({ rating: null, revision: 5 }));
    renderDetail({
      state: {
        ...movieState,
        watched_at: "2026-08-01T10:00:00Z",
        rating: 4,
        revision: 4,
      },
    });

    // An already-rated movie opens collapsed, so clearing starts by reopening
    // the stars — which is also where `Clear rating` deliberately lives, one
    // step away from a value somebody already recorded.
    await userEvent.click(
      screen.getByRole("button", { name: "Change rating for The Handmaiden" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Clear rating" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Rating removed. The Handmaiden stays in watched history.",
    );
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

    expect(await screen.findByRole("button", { name: "Undo not for me" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(
      "excluded from recommendations and can be undone",
    );
  });
});
