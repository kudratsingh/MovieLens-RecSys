import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { LibraryExperience } from "@/components/library/library-experience";
import { LibrarySpotlight } from "@/components/library/library-spotlight";
import type { LibraryMovie, MovieDetailResponse } from "@/lib/api";
import {
  createRecordedLibraryClient,
  RECORDED_PERSONA,
  recordedLibraryMovies,
} from "@/lib/fixtures/library-fixtures";
import type { LibraryClient } from "@/lib/library/client";
import {
  DEFAULT_LIBRARY_USER_ID,
  LIBRARY_PAGE_SIZE,
  type LibraryUrlState,
} from "@/lib/library/url-state";
import {
  failureState,
  loadingState,
  readyState,
  type ResourceState,
} from "@/lib/resources/state";

vi.mock("next/navigation", () => ({
  usePathname: () => "/library",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
}));

const SEEN: readonly LibraryMovie[] = recordedLibraryMovies().filter(
  (movie) => movie.state.watched_at !== null,
);

function detailFor(movie: LibraryMovie): MovieDetailResponse {
  return {
    item: {
      details: {
        backdrop_url: "/backdrops/handmaiden.svg",
        cast: [
          { name: "Kim Min-hee", character: "Lady Hideko", profile_url: null },
          { name: "Kim Tae-ri", character: "Sook-hee", profile_url: null },
        ],
        directors: ["Park Chan-wook"],
        fetched_at: "2026-08-24T09:00:00Z",
        release_date: "2016-06-01",
        runtime_minutes: 145,
        tagline: "Two women, two cons.",
        tmdb_rating: { average: 8.1, count: 4812 },
        trailer: null,
      },
      genres: [...movie.genres],
      interaction_count: 12,
      metadata_source: "reviewed-fixture",
      movie_id: movie.movie_id,
      overview: null,
      poster_url: movie.poster_url,
      release_year: movie.release_year,
      source_status: "complete",
      state: movie.state,
      title: movie.title,
      tmdb_id: "1234",
    },
    tenant_id: "demo",
    user_id: DEFAULT_LIBRARY_USER_ID,
  };
}

async function renderSpotlight(
  overrides: Partial<Parameters<typeof LibrarySpotlight>[0]> = {},
) {
  const movie = overrides.movie ?? SEEN[0];
  const props = {
    busy: false,
    hasNext: true,
    hasPrevious: false,
    href: `/movies/${movie.movie_id}`,
    movie,
    onAction: vi.fn(),
    onNext: vi.fn(),
    onPrevious: vi.fn(),
    persona: RECORDED_PERSONA,
    position: 1,
    readDetail: (): Promise<ResourceState<MovieDetailResponse>> =>
      Promise.resolve(loadingState("movie-detail")),
    total: 42,
    ...overrides,
  };
  const view = render(<LibrarySpotlight {...props} />);
  // The detail read starts on mount; flushing it here keeps a resolved
  // enrichment from landing between an assertion and the render it is about.
  await act(async () => {});
  return { ...view, props, user: userEvent.setup() };
}

describe("the spotlight is complete before the detail read answers", () => {
  it("renders the whole card from the row the list already has", async () => {
    const { container } = await renderSpotlight();
    const spotlight = screen.getByRole("region", { name: "Seen spotlight" });

    expect(within(spotlight).getByRole("heading", { level: 3 })).toHaveTextContent(
      "The Handmaiden",
    );
    // Year, genres — and no runtime yet, because nothing waits on the read.
    expect(within(spotlight).getByText("2016 · Thriller · Drama")).toBeVisible();
    expect(within(spotlight).getByText(/^Seen on /)).toBeVisible();
    expect(within(spotlight).getByText("1 of 42")).toBeVisible();
    expect(
      within(spotlight).getByRole("button", { name: "Next seen title" }),
    ).toBeEnabled();
    expect(
      within(spotlight).getByRole("button", { name: "Previous seen title" }),
    ).toBeDisabled();
    // The rating control and the confirmed removal are the row's, declared
    // through the same call, so the two surfaces cannot offer different actions.
    expect(within(spotlight).getByRole("group", { name: "Your rating" })).toBeVisible();
    expect(
      within(spotlight).getByRole("button", { name: "Remove from history" }),
    ).toBeVisible();

    expect(await axe(container)).toHaveNoViolations();
  });

  it("adds the runtime, the crowd score, and the backdrop when the read lands", async () => {
    const movie = SEEN[0];
    await renderSpotlight({ readDetail: () => Promise.resolve(readyState("movie-detail", detailFor(movie), "req-1")) });

    const spotlight = screen.getByRole("region", { name: "Seen spotlight" });
    // The vote count travels with the average here, where there is room for it.
    expect(await within(spotlight).findByText("8.1 / 10 · 4,812 ratings")).toBeVisible();
    expect(within(spotlight).getByText(/2h 25m/)).toBeVisible();
    expect(within(spotlight).getByText(/Kim Min-hee/)).toBeVisible();
    await waitFor(() =>
      expect(spotlight.querySelector(".library-spotlight-backdrop")).not.toBeNull(),
    );
  });

  it("says nothing at all when the detail read fails", async () => {
    const failed = failureState({
      status: "upstream-error",
      resource: "movie-detail",
      reason: "timeout",
      requestId: "req-detail-down",
    });
    await renderSpotlight({ readDetail: () => Promise.resolve(failed) });
    await act(async () => {});

    const spotlight = screen.getByRole("region", { name: "Seen spotlight" });
    // Progressive enhancement of a card that is already complete: no error
    // region, no retry, no request ID on screen, and the base layer intact.
    expect(within(spotlight).queryByRole("alert")).toBeNull();
    expect(within(spotlight).queryByText(/Try again/)).toBeNull();
    expect(within(spotlight).queryByText(/req-detail-down/)).toBeNull();
    expect(within(spotlight).getByRole("heading", { level: 3 })).toHaveTextContent(
      "The Handmaiden",
    );
  });

  it("abandons the read for a title the reader has moved past", async () => {
    const aborted: boolean[] = [];
    const { rerender, props } = await renderSpotlight({
      readDetail: (_movieId: number, signal: AbortSignal) => {
        signal.addEventListener("abort", () => aborted.push(true));
        return new Promise<ResourceState<MovieDetailResponse>>(() => {});
      },
    });

    await act(async () => {
      rerender(<LibrarySpotlight {...props} movie={SEEN[1]} position={2} />);
    });

    expect(aborted).toEqual([true]);
  });
});

describe("moving the spotlight", () => {
  it("reports the move to the route and announces title and position", async () => {
    const onNext = vi.fn();
    const { props, rerender, user } = await renderSpotlight({ onNext });

    await user.click(screen.getByRole("button", { name: "Next seen title" }));
    expect(onNext).toHaveBeenCalledTimes(1);

    // The route owns the window, so the announcement arrives with the movie the
    // route hands back — which is exactly what a reader would hear.
    await act(async () => {
      rerender(
        <LibrarySpotlight {...props} movie={SEEN[1]} onNext={onNext} position={2} />,
      );
    });
    await waitFor(() =>
      expect(
        screen.getByText("In the Mood for Love, 2000. 2 of 42 in Seen."),
      ).toBeInTheDocument(),
    );
  });

  it("moves on the arrow keys when the spotlight has focus", async () => {
    const onNext = vi.fn();
    const onPrevious = vi.fn();
    const { user } = await renderSpotlight({ hasPrevious: true, onNext, onPrevious });

    screen.getByRole("button", { name: "Next seen title" }).focus();
    await user.keyboard("{ArrowRight}");
    await user.keyboard("{ArrowLeft}");

    expect(onNext).toHaveBeenCalledTimes(1);
    expect(onPrevious).toHaveBeenCalledTimes(1);
  });

  it("leaves the arrow keys to the star row, which documents that binding", async () => {
    const onNext = vi.fn();
    const onPrevious = vi.fn();
    // An unrated title opens with the five stars rather than the chip, which is
    // where the roving tab stop lives.
    const unrated = SEEN.find((movie) => movie.state.rating === null);
    const { user } = await renderSpotlight({
      hasPrevious: true,
      movie: unrated,
      onNext,
      onPrevious,
    });

    const stars = screen.getByRole("group", { name: "Your rating" });
    // Focusing a star previews the value it would set, which is a state change
    // of the control's own — hence the wrap, not because the test needs it.
    await act(async () => within(stars).getAllByRole("button")[0].focus());
    await user.keyboard("{ArrowRight}");

    expect(onNext).not.toHaveBeenCalled();
    expect(onPrevious).not.toHaveBeenCalled();
  });

  it("does not move focus, so a repeated Next keeps working", async () => {
    const { props, rerender, user } = await renderSpotlight();
    const next = screen.getByRole("button", { name: "Next seen title" });

    await user.click(next);
    await act(async () => {
      rerender(<LibrarySpotlight {...props} movie={SEEN[1]} position={2} />);
    });

    expect(screen.getByRole("button", { name: "Next seen title" })).toHaveFocus();
  });
});

/*
 * The spotlight inside the route, because the interesting half of a removal —
 * the row leaving, the spotlight advancing, the rating landing back on the row
 * — is a conversation between the two.
 */
function urlState(overrides: Partial<LibraryUrlState> = {}): LibraryUrlState {
  return {
    userId: DEFAULT_LIBRARY_USER_ID,
    tab: "history",
    sort: "recent",
    query: "",
    genre: null,
    yearFrom: null,
    yearTo: null,
    cursor: null,
    ...overrides,
  };
}

async function renderSeenTab(client: LibraryClient = createRecordedLibraryClient()) {
  const state = urlState();
  const [library, taste] = await Promise.all([
    client.readLibrary({ ...state, limit: LIBRARY_PAGE_SIZE }),
    client.readTasteProfile(state.userId),
  ]);
  const view = render(
    <LibraryExperience
      actorName="demo@example.com"
      client={client}
      initialLibrary={library}
      initialTaste={taste}
      initialUrlState={state}
      personaLabel={RECORDED_PERSONA}
      personaResolved
    />,
  );
  await act(async () => {});
  return { ...view, user: userEvent.setup() };
}

describe("the spotlight and the rows agree", () => {
  it("advances past a title the reader removes from history", async () => {
    const { user } = await renderSeenTab();
    const spotlight = screen.getByRole("region", { name: "Seen spotlight" });
    const first = within(spotlight).getByRole("heading", { level: 3 }).textContent;

    await user.click(
      within(spotlight).getByRole("button", { name: "Remove from history" }),
    );
    const confirm = within(spotlight).getByRole("group", { name: /^Confirm removing/ });
    await user.click(within(confirm).getByRole("button", { name: "Remove from history" }));

    await waitFor(() =>
      expect(within(spotlight).getByRole("heading", { level: 3 })).not.toHaveTextContent(
        first ?? "",
      ),
    );
    // The row says what happened to it rather than vanishing under the reader,
    // which is the collection's own rule; the spotlight cannot feature a title
    // the reader has just taken out.
    expect(await screen.findByText(/No longer watched/)).toBeVisible();
  });

  it("relays a rating committed in the spotlight to the row for the same movie", async () => {
    const { user } = await renderSeenTab();
    const spotlight = screen.getByRole("region", { name: "Seen spotlight" });
    const stars = within(spotlight).getByRole("group", { name: "Your rating" });

    await user.click(within(stars).getByRole("button", { name: /^Change rating for/ }));
    await user.click(within(stars).getByRole("button", { name: /^3 stars for/ }));

    expect(await screen.findByText(/Rating saved for/)).toBeInTheDocument();
    const row = screen
      .getAllByRole("listitem")
      .find((item) => item.querySelector("#library-movie-101"));
    await waitFor(() =>
      expect(within(row as HTMLElement).getByText(/Rated 3\.0 of 5/)).toBeVisible(),
    );
  });
});
