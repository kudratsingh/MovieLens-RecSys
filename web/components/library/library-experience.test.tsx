import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LibraryExperience } from "@/components/library/library-experience";
import {
  createRecordedLibraryClient,
  RECORDED_PERSONA,
  recordedLibraryCursor,
} from "@/lib/fixtures/library-fixtures";
import type { LibraryClient } from "@/lib/library/client";
import {
  DEFAULT_LIBRARY_USER_ID,
  LIBRARY_PAGE_SIZE,
  type LibraryTab,
  type LibraryUrlState,
} from "@/lib/library/url-state";
import { failureState } from "@/lib/resources/state";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/library",
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

beforeEach(() => replace.mockClear());

function urlState(overrides: Partial<LibraryUrlState> = {}): LibraryUrlState {
  return {
    userId: DEFAULT_LIBRARY_USER_ID,
    tab: "rated",
    sort: "recent",
    query: "",
    genre: null,
    yearFrom: null,
    yearTo: null,
    cursor: null,
    ...overrides,
  };
}

async function renderLibrary(
  client: LibraryClient = createRecordedLibraryClient(),
  state = urlState(),
) {
  const view = async (target: LibraryUrlState) => {
    const [library, taste] = await Promise.all([
      client.readLibrary({ ...target, limit: LIBRARY_PAGE_SIZE }),
      client.readTasteProfile(target.userId),
    ]);
    return (
      <LibraryExperience
        actorName="demo@example.com"
        client={client}
        initialLibrary={library}
        initialTaste={taste}
        initialUrlState={target}
        personaLabel={RECORDED_PERSONA}
        personaResolved
      />
    );
  };

  const result = render(await view(state));
  // The Seen spotlight asks for its current title's enriched record as soon as
  // it mounts. Flushing that here keeps every test's first assertion from
  // racing a state update it did not ask for.
  await act(async () => {});
  return {
    ...result,
    user: userEvent.setup(),
    /**
     * What a browser Back actually delivers: the server re-renders for the
     * popped URL and hands the route a fresh parse and the page that goes with
     * it. The route used to read both exactly once and ignore every later one.
     */
    async serverRenderFor(target: LibraryUrlState) {
      result.rerender(await view(target));
      await act(async () => {});
    },
  };
}

function failingLibrary(base: LibraryClient, tab: LibraryTab): LibraryClient {
  return {
    ...base,
    readLibrary: (options) =>
      options.tab === tab
        ? Promise.resolve(
            failureState({
              status: "upstream-error",
              resource: "library",
              reason: "server",
              requestId: "req-library-down",
            }),
          )
        : base.readLibrary(options),
  };
}

describe("the Library route names the persona it is showing", () => {
  it("separates the signed-in actor from the persona whose state this is", async () => {
    const { container } = await renderLibrary();

    expect(
      screen.getByRole("heading", { level: 1, name: "A record of what moved you." }),
    ).toBeVisible();
    // The route no longer repeats `Exploring as {persona}`: the shell header
    // prints it, and this block would print it again 40px below. What stays
    // here is the part the shell cannot say — that the actor and the persona
    // are two identities.
    expect(screen.queryByText(`Exploring as ${RECORDED_PERSONA}`)).not.toBeInTheDocument();
    expect(
      screen.getByText(/not the signed-in actor's private library/i),
    ).toHaveTextContent("demo@example.com");
    expect(
      screen.getByText(/not the signed-in actor's private library/i),
    ).toHaveTextContent(RECORDED_PERSONA);
    expect(screen.queryByText(/your library/i)).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("collections load independently and own their URL state", () => {
  it("shows the rated collection first with its counts", async () => {
    await renderLibrary();

    expect(screen.getByRole("tab", { name: "Rated 12" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const list = screen.getByRole("list", { name: "Rated movies" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(LIBRARY_PAGE_SIZE);
  });

  it("writes the selected tab to the URL and loads that collection on its own", async () => {
    const { user } = await renderLibrary();

    await user.click(screen.getByRole("tab", { name: "Watchlist 4" }));

    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&tab=watchlist`,
      { scroll: false },
    );
    expect(await screen.findByRole("list", { name: "Watchlist movies" })).toBeVisible();
    expect(screen.getByText("Aftersun")).toBeVisible();
  });

  it("serializes sort and filter without inventing a total count", async () => {
    const { user } = await renderLibrary();

    await user.selectOptions(screen.getByLabelText("Sort"), "title");
    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&sort=title`,
      { scroll: false },
    );

    await user.type(screen.getByLabelText(/Filter rated by title/), "burning");
    await user.click(screen.getByRole("button", { name: "Filter" }));
    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&sort=title&q=burning`,
      { scroll: false },
    );

    const list = await screen.findByRole("list", { name: "Rated movies" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByText(/of 12 results/)).not.toBeInTheDocument();
  });

  it("ranks the rated collection by release year and by crowd score", async () => {
    const { user } = await renderLibrary();

    const sort = screen.getByLabelText("Sort");
    expect(
      within(sort).getAllByRole("option").map((option) => option.textContent),
    ).toEqual([
      "Most recent",
      "Title",
      "Highest rated",
      "Newest release",
      "Highest TMDB score",
    ]);

    // Both keys come off the movie rather than off the star value, so the row
    // that leads each ordering is the one whose printed fact says so.
    await user.selectOptions(sort, "release");
    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&sort=release`,
      { scroll: false },
    );
    const byRelease = await screen.findByRole("list", { name: "Rated movies" });
    expect(within(byRelease).getAllByRole("listitem")[0]).toHaveTextContent(
      "Decision to Leave",
    );

    await user.selectOptions(sort, "tmdb");
    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&sort=tmdb`,
      { scroll: false },
    );
    const byScore = await screen.findByRole("list", { name: "Rated movies" });
    const leader = within(byScore).getAllByRole("listitem")[0];
    expect(leader).toHaveTextContent("Parasite");
    expect(leader).toHaveTextContent("TMDB 8.5");
  });

  it("follows a browser Back to the collection the URL names", async () => {
    const client = createRecordedLibraryClient();
    const reads = vi.spyOn(client, "readLibrary");
    const { user, serverRenderFor } = await renderLibrary(client);

    await user.click(screen.getByRole("tab", { name: "Watchlist 4" }));
    expect(await screen.findByRole("list", { name: "Watchlist movies" })).toBeVisible();
    // `router.replace` re-runs the server component, which is the render this
    // route used to discard.
    await serverRenderFor(urlState({ tab: "watchlist" }));
    const beforeBack = reads.mock.calls.length;

    await serverRenderFor(urlState());

    expect(screen.getByRole("list", { name: "Rated movies" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Rated 12" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("Titles with a star value")).toBeVisible();
    // One read, and it is the harness resolving the payload the server would
    // have carried. The route adopted it rather than asking again: a Back into
    // a collection must not cost a second round trip.
    expect(reads.mock.calls.length).toBe(beforeBack + 1);
  });

  it("keeps an accumulated window when the server re-renders for the same view", async () => {
    // `Load more` writes its resume cursor to the URL, so the server re-renders
    // with the *last* page rather than the accumulated window. Adopting that
    // would silently throw away everything the reader had already paged in.
    const { user, serverRenderFor } = await renderLibrary(
      createRecordedLibraryClient(),
      urlState({ tab: "history" }),
    );

    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Seen movies" })).getAllByRole(
          "listitem",
        ),
      ).toHaveLength(15),
    );

    await serverRenderFor(
      urlState({ tab: "history", cursor: recordedLibraryCursor(urlState({ tab: "history" }), 12) }),
    );

    expect(
      within(screen.getByRole("list", { name: "Seen movies" })).getAllByRole(
        "listitem",
      ),
    ).toHaveLength(15);
  });

  it("drops the rating sort when it moves to a collection without ratings", async () => {
    const { user } = await renderLibrary(
      createRecordedLibraryClient(),
      urlState({ sort: "rating" }),
    );

    await user.click(screen.getByRole("tab", { name: "Watchlist 4" }));

    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&tab=watchlist`,
      { scroll: false },
    );
  });

  it("appends the next cursor page without repeating a row", async () => {
    const { user } = await renderLibrary(
      createRecordedLibraryClient(),
      urlState({ tab: "history" }),
    );

    const before = within(
      screen.getByRole("list", { name: "Seen movies" }),
    ).getAllByRole("listitem");
    expect(before).toHaveLength(LIBRARY_PAGE_SIZE);

    await user.click(screen.getByRole("button", { name: "Load more" }));

    await waitFor(() =>
      expect(
        within(screen.getByRole("list", { name: "Seen movies" })).getAllByRole(
          "listitem",
        ),
      ).toHaveLength(15),
    );
    // Scoped to the list: the spotlight above it prints the current title as
    // its own heading, which is the same movie deliberately, not a repeat row.
    const titles = within(screen.getByRole("list", { name: "Seen movies" }))
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(new Set(titles).size).toBe(titles.length);
    expect(replace).toHaveBeenLastCalledWith(
      `/library?userId=${DEFAULT_LIBRARY_USER_ID}&tab=history&cursor=${encodeURIComponent(
        recordedLibraryCursor(urlState({ tab: "history" }), 12),
      )}`,
      { scroll: false },
    );
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });
});

describe("each collection state stands on its own", () => {
  it("offers a path to Browse when a collection is empty", async () => {
    const { container } = await renderLibrary(
      createRecordedLibraryClient({ emptyTabs: ["watchlist"] }),
      urlState({ tab: "watchlist" }),
    );

    expect(screen.getByText("Nothing saved yet")).toBeVisible();
    expect(screen.getByRole("link", { name: "Browse the catalog" })).toHaveAttribute(
      "href",
      "/browse",
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("explains an empty filter result without claiming the collection is empty", async () => {
    const { user } = await renderLibrary();

    await user.type(screen.getByLabelText(/Filter rated by title/), "zzzz");
    await user.click(screen.getByRole("button", { name: "Filter" }));

    expect(await screen.findByText("No matches in this collection")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear the filter" }));
    expect(await screen.findByRole("list", { name: "Rated movies" })).toBeVisible();
  });

  it("keeps the other collections usable when one of them fails", async () => {
    const base = createRecordedLibraryClient();
    const { user, container } = await renderLibrary(failingLibrary(base, "rated"));

    const problem = screen.getByRole("alert", {
      // Named by its headline rather than by the transport status enum.
      name: "Rated collection could not be loaded",
    });
    expect(problem).toHaveTextContent("The recommendation API returned an error.");
    expect(problem).toHaveTextContent("req-library-down");
    expect(await axe(container)).toHaveNoViolations();

    // No collection has answered, so no tab can show a count yet.
    await user.click(screen.getByRole("tab", { name: "Watchlist" }));
    expect(await screen.findByRole("list", { name: "Watchlist movies" })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers a reauthentication path when the session has expired", async () => {
    const base = createRecordedLibraryClient();
    await renderLibrary({
      ...base,
      readLibrary: () =>
        Promise.resolve(
          failureState({
            status: "auth-expired",
            resource: "library",
            reason: "session-expired",
            requestId: "req-expired",
          }),
        ),
    });

    expect(screen.getByRole("alert")).toHaveTextContent("Your session expired");
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/");
  });

  it("keeps a failing ratings summary away from the collection", async () => {
    await renderLibrary(createRecordedLibraryClient({ failing: ["taste-profile"] }));

    expect(screen.getByRole("list", { name: "Rated movies" })).toBeVisible();
    expect(
      screen.getByRole("alert", { name: "Live ratings summary could not be loaded" }),
    ).toBeVisible();
  });
});

describe("mutations reconcile canonical state", () => {
  it("edits a rating, reconciles the committed response, and restores focus", async () => {
    const client = createRecordedLibraryClient();
    const mutate = vi.spyOn(client, "mutate");
    const { user } = await renderLibrary(client);

    const rating = screen.getByLabelText("Rating for Memories of Murder");
    await user.selectOptions(rating, "3");

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    const request = mutate.mock.calls[0][0];
    expect(request).toMatchObject({
      resource: "rating",
      method: "PUT",
      rating: 3,
      movieId: 103,
      expectedRevision: 1,
    });
    expect(request.idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);

    await waitFor(() => expect(rating).toHaveValue("3"));
    expect(screen.getByText(/Watched Aug 16, 2026 · Rated 3.0 of 5/)).toBeVisible();
    expect(screen.getByText(/Rating saved for Memories of Murder/)).toHaveTextContent(
      "star value is display feedback only",
    );
    await waitFor(() => expect(rating).toHaveFocus());
  });

  it("rolls back and says so when the API refuses the write", async () => {
    const client = createRecordedLibraryClient();
    const { user } = await renderLibrary({
      ...client,
      mutate: () =>
        Promise.resolve({
          status: "failed" as const,
          failure: failureState({
            status: "upstream-error",
            resource: "library",
            reason: "timeout",
            requestId: "req-write-timeout",
          }),
        }),
    });

    const rating = screen.getByLabelText("Rating for Memories of Murder");
    expect(rating).toHaveValue("4.5");
    await user.selectOptions(rating, "1");

    await waitFor(() => expect(rating).toHaveValue("4.5"));
    expect(screen.getByRole("alert")).toHaveTextContent("That change was not saved");
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(
      screen.getByText(/Could not update Memories of Murder/),
    ).toHaveTextContent(`${RECORDED_PERSONA}'s saved state was restored.`);
    await waitFor(() => expect(rating).toHaveFocus());
  });

  it("states the rule, keeps the row, and drops the retry when a transition is refused", async () => {
    // Quoted from `InvalidStateTransitionError` in `src/serving/feedback.py`.
    // It arrives on the same 409 as a stale revision; reported as a conflict it
    // told the reader the row "changed elsewhere", which nothing had.
    const rule = "a watched movie cannot be added to the watchlist";
    const client = createRecordedLibraryClient();
    const { user } = await renderLibrary({
      ...client,
      mutate: () =>
        Promise.resolve({
          status: "refused" as const,
          requestId: "req-refused",
          detail: rule,
        }),
      readState: () => {
        throw new Error("a refused transition must not trigger a canonical read");
      },
    });

    const rating = screen.getByLabelText("Rating for Memories of Murder");
    await user.selectOptions(rating, "1");

    expect(await screen.findByText(new RegExp(rule))).toHaveTextContent(
      `Memories of Murder was left as it is in ${RECORDED_PERSONA}'s library.`,
    );
    expect(screen.queryByText(/changed elsewhere/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(rating).toHaveValue("4.5");
  });

  it("shows what is stored when the row was written from a stale revision", async () => {
    const client = createRecordedLibraryClient();
    const { user } = await renderLibrary({
      ...client,
      mutate: () =>
        Promise.resolve({
          status: "conflict" as const,
          requestId: "req-conflict",
          detail: "state revision does not match",
        }),
    });

    await user.selectOptions(screen.getByLabelText("Rating for Memories of Murder"), "1");

    expect(
      await screen.findByText(/Memories of Murder changed elsewhere/),
    ).toHaveTextContent(`${RECORDED_PERSONA}'s latest saved state is shown.`);
    expect(screen.getByLabelText("Rating for Memories of Murder")).toHaveValue("4.5");
  });
});

describe("deleting a rating is not removing history", () => {
  it("clears the star and leaves the movie in History without a confirmation", async () => {
    const client = createRecordedLibraryClient();
    const { user } = await renderLibrary(client, urlState({ tab: "history" }));

    const row = screen
      .getByRole("heading", { level: 3, name: "Memories of Murder" })
      .closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Remove rating" }));

    expect(
      await screen.findByText(/Rating removed from Memories of Murder/),
    ).toHaveTextContent("still watched history");
    expect(within(row).getByText(/Watched Aug 16, 2026 · Not rated/)).toBeVisible();
    expect(within(row).getByRole("button", { name: "Remove from history" })).toBeVisible();
    expect(screen.queryByRole("group", { name: /Confirm removing/ })).not.toBeInTheDocument();
  });

  it("states the consequence and requires a confirmation before removing history", async () => {
    const client = createRecordedLibraryClient();
    const mutate = vi.spyOn(client, "mutate");
    const { user, container } = await renderLibrary(client, urlState({ tab: "history" }));

    const row = screen
      .getByRole("heading", { level: 3, name: "Memories of Murder" })
      .closest("li") as HTMLElement;
    const trigger = within(row).getByRole("button", { name: "Remove from history" });
    await user.click(trigger);

    const confirm = screen.getByRole("group", {
      name: "Confirm removing Memories of Murder from watched history",
    });
    expect(confirm).toHaveTextContent(
      "deletes the watched interaction and its rating",
    );
    expect(confirm).toHaveTextContent(
      `stops counting as a positive signal for ${RECORDED_PERSONA}`,
    );
    expect(mutate).not.toHaveBeenCalled();
    expect(await axe(container)).toHaveNoViolations();

    await user.click(within(confirm).getByRole("button", { name: "Remove from history" }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0][0]).toMatchObject({
      resource: "watched",
      method: "DELETE",
    });
    expect(
      await screen.findByText(/removed from Action Fan's watched history/),
    ).toBeVisible();
    expect(within(row).getByText(/No longer watched/)).toBeVisible();
  });

  it("cancels without writing and returns focus to the control that opened it", async () => {
    const client = createRecordedLibraryClient();
    const mutate = vi.spyOn(client, "mutate");
    const { user } = await renderLibrary(client, urlState({ tab: "history" }));

    const row = screen
      .getByRole("heading", { level: 3, name: "Memories of Murder" })
      .closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Remove from history" }));
    await user.keyboard("{Escape}");

    expect(mutate).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(within(row).getByRole("button", { name: "Remove from history" })).toHaveFocus(),
    );
  });
});

describe("watchlist and the ratings summary make no model claim", () => {
  it("says plainly that saving changes nothing about recommendations", async () => {
    const { user } = await renderLibrary(
      createRecordedLibraryClient(),
      urlState({ tab: "watchlist" }),
    );

    expect(
      screen.getByText(/Saving is organizational only/),
    ).toHaveTextContent("seeds no candidates, excludes nothing, and changes no model features");

    const row = screen
      .getByRole("heading", { level: 3, name: "Aftersun" })
      .closest("li") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Remove from watchlist" }));

    const announcement = await screen.findByText(/Aftersun removed from Action Fan's watchlist/);
    expect(announcement).not.toHaveTextContent(/recommend|model|rank/i);
  });

  it("keeps dismissal reversible and calls it an exclusion, not a dislike", async () => {
    const { user } = await renderLibrary(
      createRecordedLibraryClient(),
      urlState({ tab: "watchlist" }),
    );

    const row = screen
      .getByRole("heading", { level: 3, name: "Anatomy of a Fall" })
      .closest("li") as HTMLElement;
    expect(within(row).getByText(/Excluded from recommendations/)).toBeVisible();

    await user.click(within(row).getByRole("button", { name: "Undo not for me" }));
    expect(
      await screen.findByText(/Anatomy of a Fall is eligible for Action Fan's recommendations again/),
    ).toBeVisible();
  });

  it("presents the ratings summary as a live read, never as the ranker's explanation", async () => {
    await renderLibrary();

    const summary = screen
      .getByRole("heading", { name: /A readable outline/ })
      .closest("div") as HTMLElement;
    expect(summary).toHaveTextContent(
      "This summary is not a deployed-model explanation.",
    );
    expect(summary).toHaveTextContent("Recomputed from 12 ratings on every read");
    expect(summary).toHaveTextContent("source live-ratings-v1");
    expect(summary).not.toHaveTextContent(/LightGBM|ranker|because you liked|match %/i);
  });
});
