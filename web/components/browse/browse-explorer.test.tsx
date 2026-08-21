import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowseExplorer } from "@/components/browse/browse-explorer";
import type { CatalogItem, CatalogResponse } from "@/lib/api";
import {
  browseSnapshotKey,
  saveBrowseSnapshot,
  snapshotFromWindow,
} from "@/lib/browse/restoration";
import { appendCatalogPage, startWindow } from "@/lib/browse/window";

const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/browse",
  useSearchParams: () => nav.search,
  useRouter: () => ({
    replace: nav.replace,
    push: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
}));

const USER_ID = 900000101;
const ENDPOINT = `/api/users/${USER_ID}/catalog`;

function item(overrides: Partial<CatalogItem> & { movie_id: number }): CatalogItem {
  return {
    title: `Title ${overrides.movie_id}`,
    genres: ["Drama"],
    tmdb_id: null,
    release_year: 2016,
    poster_url: null,
    overview: null,
    metadata_source: "movielens",
    source_status: "unavailable",
    state: null,
    interaction_count: 12,
    ...overrides,
  };
}

function response(items: CatalogItem[], nextCursor: string | null = null): CatalogResponse {
  return {
    tenant_id: "demo",
    user_id: USER_ID,
    items,
    page: { has_more: nextCursor !== null, next_cursor: nextCursor },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answers each catalog read in turn, so a test can script a page sequence. */
function scriptFetch(...queued: Response[]) {
  const calls: string[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return queued.shift() ?? jsonResponse(response([]));
  });
  globalThis.fetch = impl as unknown as typeof fetch;
  return { calls, impl };
}

function renderBrowse(persistedParams?: Record<string, string>) {
  return render(
    <main>
      <BrowseExplorer
        browsePath="/browse"
        catalogEndpoint={ENDPOINT}
        movieBasePath="/movies"
        persistedParams={persistedParams}
        userId={USER_ID}
      />
    </main>,
  );
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  nav.search = new URLSearchParams();
  nav.replace.mockClear();
  window.sessionStorage.clear();
  Object.defineProperty(window, "scrollTo", { configurable: true, value: vi.fn() });
  window.history.replaceState(null, "", "/browse");
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("Browse grid states", () => {
  it("reserves the grid while loading and then renders the results", async () => {
    scriptFetch(jsonResponse(response([item({ movie_id: 1 }), item({ movie_id: 2 })])));
    const { container } = renderBrowse();

    expect(screen.getByRole("status")).toHaveTextContent("Loading catalog results");
    expect(await screen.findByRole("list", { name: "Browse results" })).toBeVisible();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(/titles loaded/)).toHaveTextContent(
      "2 titles loaded · end of results",
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("names an incomplete metadata record instead of leaving a blank card", async () => {
    scriptFetch(
      jsonResponse(
        response([
          item({
            movie_id: 1,
            title: "Complete Record",
            metadata_source: "reviewed-fixture",
            source_status: "complete",
            poster_url: "/posters/burning.svg",
            overview: "A synopsis exists for this one.",
          }),
          item({
            movie_id: 2,
            title: "Partial Record",
            metadata_source: "tmdb-snapshot",
            source_status: "partial",
            overview: "Overview but no poster.",
          }),
          item({ movie_id: 3, title: "Unavailable Record", release_year: null, genres: [] }),
        ]),
      ),
    );
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    const cards = screen.getAllByRole("listitem");

    expect(within(cards[0]).queryByText("Partial details")).not.toBeInTheDocument();
    expect(within(cards[1]).getByText("Partial details")).toBeVisible();
    expect(within(cards[2]).getByText("Details unavailable")).toBeVisible();
    expect(within(cards[2]).getByText(/Year unknown · Genre unavailable/)).toBeVisible();
    // Deterministic local artwork, never a per-card third-party lookup.
    expect(within(cards[1]).getByTestId("poster-fallback")).toBeVisible();
  });

  it("makes no request beyond the catalog read itself", async () => {
    const { calls } = scriptFetch(
      jsonResponse(
        response([
          item({ movie_id: 1, poster_url: "https://image.tmdb.org/t/p/w500/a.jpg" }),
          item({ movie_id: 2, poster_url: "https://image.tmdb.org/t/p/w500/b.jpg" }),
        ]),
      ),
    );
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain(ENDPOINT);
  });

  it("swaps a failed poster for the fallback without dropping a card", async () => {
    scriptFetch(
      jsonResponse(
        response([
          item({ movie_id: 1, title: "Broken Poster", poster_url: "/posters/burning.svg" }),
          item({ movie_id: 2 }),
        ]),
      ),
    );
    const { container } = renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    const before = screen.getAllByRole("listitem").length;
    // The poster is decorative beside a visible title, so it carries an empty
    // alt; the failure is triggered on the element rather than by its name.
    fireEvent.error(container.querySelector("img")!);

    expect(screen.getAllByRole("listitem")).toHaveLength(before);
    expect(screen.getByRole("link", { name: "Open Broken Poster" })).toBeVisible();
    expect(screen.getAllByTestId("poster-fallback").length).toBe(2);
  });

  it("offers a way out of an empty result rather than an error", async () => {
    scriptFetch(jsonResponse(response([])));
    const { container } = renderBrowse();

    expect(await screen.findByText("No movies match this cut")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Clear search and filters" }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("reports an upstream failure as its own state and retries on demand", async () => {
    scriptFetch(
      jsonResponse({ detail: "upstream" }, 502),
      jsonResponse(response([item({ movie_id: 1 })])),
    );
    const { container } = renderBrowse();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Catalog could not be loaded");
    expect(await axe(container)).toHaveNoViolations();

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("list", { name: "Browse results" })).toBeVisible();
  });

  it("routes an expired session to reauthentication", async () => {
    scriptFetch(jsonResponse({ detail: "expired" }, 401));
    renderBrowse();

    expect(await screen.findByText("Your session expired")).toBeVisible();
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});

describe("cursor continuation", () => {
  it("appends the next page without repeating a movie", async () => {
    scriptFetch(
      jsonResponse(response([item({ movie_id: 1 }), item({ movie_id: 2 })], "cursor-2")),
      jsonResponse(response([item({ movie_id: 2 }), item({ movie_id: 3 })])),
    );
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    await userEvent.click(screen.getByRole("button", { name: "Load more movies" }));

    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(3));
    expect(screen.getByText(/titles loaded/)).toHaveTextContent(
      "3 titles loaded · end of results",
    );
    expect(screen.queryByRole("button", { name: "Load more movies" })).not.toBeInTheDocument();
    expect(screen.getByText("That is every title matching these filters.")).toBeVisible();
  });

  it("carries the resume point into the URL without inventing a total", async () => {
    const { calls } = scriptFetch(
      jsonResponse(response([item({ movie_id: 1 })], "cursor-2")),
      jsonResponse(response([item({ movie_id: 2 })])),
    );
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    await userEvent.click(screen.getByRole("button", { name: "Load more movies" }));

    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toContain("cursor=cursor-2");
    expect(window.location.search).toContain("cursor=cursor-2");
    expect(screen.queryByText(/of \d+/)).not.toBeInTheDocument();
  });

  it("starts over with a plain notice when the endpoint rejects a stale cursor", async () => {
    nav.search = new URLSearchParams("q=burning&cursor=from-an-old-link");
    const { calls } = scriptFetch(
      jsonResponse({ detail: "catalog cursor is invalid for this query" }, 400),
      jsonResponse(response([item({ movie_id: 1 })])),
    );
    renderBrowse();

    expect(
      await screen.findByText(/no longer matches these filters/),
    ).toBeVisible();
    expect(await screen.findByRole("list", { name: "Browse results" })).toBeVisible();
    // The retry drops the cursor rather than reporting an outage.
    expect(calls[0]).toContain("cursor=from-an-old-link");
    expect(calls[1]).not.toContain("cursor=");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("query state in the URL", () => {
  it("writes a filter change to the address and drops the cursor with it", async () => {
    nav.search = new URLSearchParams("cursor=deep-page");
    scriptFetch(jsonResponse(response([item({ movie_id: 1 })])));
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    await userEvent.click(screen.getByRole("button", { name: "Filters" }));
    await userEvent.click(screen.getByRole("button", { name: "Drama" }));

    expect(nav.replace).toHaveBeenCalledWith("/browse?genre=Drama", { scroll: false });
  });

  it("keeps the selected persona across a filter edit and a detail hop", async () => {
    scriptFetch(jsonResponse(response([item({ movie_id: 7, title: "Burning" })])));
    renderBrowse({ user: String(USER_ID) });

    const link = await screen.findByRole("link", { name: "Open Burning" });
    expect(link.getAttribute("href")).toContain(`user=${USER_ID}`);
    expect(decodeURIComponent(link.getAttribute("href") ?? "")).toContain(
      `returnTo=/browse?user=${USER_ID}`,
    );

    await userEvent.click(screen.getByRole("button", { name: "Filters" }));
    await userEvent.click(screen.getByRole("button", { name: "Drama" }));
    expect(nav.replace).toHaveBeenCalledWith(
      `/browse?user=${USER_ID}&genre=Drama`,
      { scroll: false },
    );

    await userEvent.click(screen.getByRole("button", { name: "Clear all filters" }));
    expect(nav.replace).toHaveBeenLastCalledWith(`/browse?user=${USER_ID}`, {
      scroll: false,
    });
  });

  it("submits a search as a query change, not a client-side filter", async () => {
    scriptFetch(jsonResponse(response([item({ movie_id: 1 })])));
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    await userEvent.type(screen.getByRole("searchbox"), "  burning  ");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(nav.replace).toHaveBeenCalledWith("/browse?q=burning", { scroll: false });
  });
});

describe("returning from a movie", () => {
  it("restores the loaded window and the scroll position without refetching", async () => {
    const restored = appendCatalogPage(startWindow(""), response([
      item({ movie_id: 1, title: "Restored One" }),
      item({ movie_id: 2, title: "Restored Two" }),
    ], "cursor-2"));
    saveBrowseSnapshot(
      window.sessionStorage,
      browseSnapshotKey(USER_ID, ""),
      snapshotFromWindow(restored, { scrollY: 980 }),
    );
    const { calls } = scriptFetch();
    renderBrowse();

    expect(await screen.findByRole("link", { name: "Open Restored One" })).toBeVisible();
    expect(calls).toHaveLength(0);
    await waitFor(() =>
      expect(window.scrollTo).toHaveBeenCalledWith({ top: 980, behavior: "instant" }),
    );
    expect(screen.getByRole("button", { name: "Load more movies" })).toBeVisible();
  });

  it("carries the current query onto every detail link so the way back is exact", async () => {
    nav.search = new URLSearchParams("q=burning&sort=newest");
    scriptFetch(jsonResponse(response([item({ movie_id: 7, title: "Burning" })])));
    renderBrowse();

    const link = await screen.findByRole("link", { name: "Open Burning" });
    expect(link.getAttribute("href")).toContain("/movies/7?");
    expect(decodeURIComponent(link.getAttribute("href") ?? "")).toContain(
      "returnTo=/browse?q=burning&sort=newest",
    );
  });

  it("ignores a stored window that belongs to a different query", async () => {
    const stored = appendCatalogPage(
      startWindow("q=other"),
      response([item({ movie_id: 1, title: "Wrong Query" })]),
    );
    saveBrowseSnapshot(
      window.sessionStorage,
      browseSnapshotKey(USER_ID, "q=other"),
      snapshotFromWindow(stored, { scrollY: 500 }),
    );
    scriptFetch(jsonResponse(response([item({ movie_id: 9, title: "Fresh" })])));
    renderBrowse();

    expect(await screen.findByRole("link", { name: "Open Fresh" })).toBeVisible();
    expect(screen.queryByText("Wrong Query")).not.toBeInTheDocument();
  });
});

describe("card state changes", () => {
  it("reconciles a committed watchlist change without moving the card", async () => {
    const committed = jsonResponse({
      request_id: "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001",
      replayed: false,
      outcome: "changed",
      state: {
        tenant_id: "demo",
        user_id: USER_ID,
        movie_id: 2,
        rating: null,
        rating_updated_at: null,
        watched_at: null,
        watchlisted_at: "2026-08-21T10:00:00Z",
        dismissed_at: null,
        revision: 1,
        updated_at: "2026-08-21T10:00:00Z",
      },
    });
    const impl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/catalog")) {
        return jsonResponse(
          response([
            item({ movie_id: 1, title: "First Movie" }),
            item({ movie_id: 2, title: "Second Movie" }),
            item({ movie_id: 3, title: "Third Movie" }),
          ]),
        );
      }
      if (url.includes("/api/auth/csrf")) return jsonResponse({ csrfToken: "token" });
      return committed;
    });
    globalThis.fetch = impl as unknown as typeof fetch;
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    const order = () =>
      screen.getAllByRole("listitem").map((cell) => within(cell).getAllByRole("link")[0].textContent);
    const before = order();

    await userEvent.click(
      screen.getByRole("button", { name: "Add Second Movie to watchlist" }),
    );

    expect(
      await screen.findByRole("button", { name: "Remove Second Movie from watchlist" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(order()).toEqual(before);
  });
});

describe("keyboard reach", () => {
  it("walks the grid without a pointer", async () => {
    scriptFetch(
      jsonResponse(
        response([
          item({ movie_id: 1, title: "First Movie" }),
          item({ movie_id: 2, title: "Second Movie" }),
        ]),
      ),
    );
    renderBrowse();

    await screen.findByRole("list", { name: "Browse results" });
    screen.getByRole("link", { name: "Open First Movie" }).focus();

    await userEvent.tab();
    expect(document.activeElement).toHaveTextContent("First Movie");

    await userEvent.tab();
    expect(document.activeElement).toHaveAccessibleName(
      "Add First Movie to watchlist",
    );

    await userEvent.tab();
    expect(document.activeElement).toHaveAccessibleName("Open Second Movie");
  });
});
