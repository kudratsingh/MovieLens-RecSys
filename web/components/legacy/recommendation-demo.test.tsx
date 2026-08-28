import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecommendationDemo } from "@/components/legacy/recommendation-demo";
import type {
  CatalogItem,
  MovieState,
  PersonaResponse,
  UserDashboard,
} from "@/lib/api";

/**
 * The retained rollback surface, exercised for the one thing on it that
 * destroys data.
 *
 * `Clear ratings` deletes every rating the selected persona holds and used to
 * fire on a single unguarded click, from a page every product footer links to.
 */

const USER_ID = 900000101;

const PERSONAS: PersonaResponse = {
  tenant_id: "demo",
  items: [
    {
      user_id: USER_ID,
      slug: "action-fan",
      display_name: "Action Fan",
      description: "Watches action and thrillers.",
    },
  ],
};

function movieState(movieId: number, rating: number | null): MovieState {
  return {
    tenant_id: "demo",
    user_id: USER_ID,
    movie_id: movieId,
    watched_at: null,
    rating,
    rating_updated_at: rating === null ? null : "2026-08-20T10:00:00Z",
    watchlisted_at: null,
    dismissed_at: null,
    revision: rating === null ? 0 : 1,
    updated_at: "2026-08-20T10:00:00Z",
  };
}

function catalogItem(movieId: number, rating: number | null): CatalogItem {
  return {
    movie_id: movieId,
    title: `Movie ${movieId}`,
    genres: ["Drama"],
    tmdb_id: null,
    release_year: 1994,
    poster_url: null,
    overview: null,
    metadata_source: "movielens",
    source_status: "unavailable",
    state: movieState(movieId, rating),
    interaction_count: 12,
  };
}

function dashboard(rated: boolean): UserDashboard {
  return {
    recommendations: {
      tenant_id: "demo",
      user_id: USER_ID,
      model_version: "demo-itemitem-v1/demo-lgbm-v1",
      policy: "item-item-cosine+lightgbm",
      serving_policy: {
        excluded_count: 4,
        filter_policy: "watched-and-dismissed-excluded-v1",
        learned: true,
        name: "item-item-cosine+lightgbm",
        positive_signal_count: 8,
        reason: "learned-two-stage",
        score_scale: "lightgbm-rank-score",
        threshold: 5,
      },
      items: [],
    },
    history: { tenant_id: "demo", user_id: USER_ID, items: [] },
    catalog: {
      tenant_id: "demo",
      user_id: USER_ID,
      items: [catalogItem(1, rated ? 4 : null), catalogItem(2, null)],
      page: { next_cursor: null, has_more: false },
    },
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string };

/** Records every request, and answers the DELETE from `deleteResponse`. */
function mockApi(deleteResponse: () => Response) {
  const calls: Call[] = [];
  let cleared = false;
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url.includes("/api/personas")) return jsonResponse(PERSONAS);
    if (url.includes("/api/auth/csrf")) return jsonResponse({ csrfToken: "token" });
    if (url.endsWith("/ratings") && method === "DELETE") {
      const response = deleteResponse();
      if (response.ok) cleared = true;
      return response;
    }
    return jsonResponse(dashboard(!cleared));
  });
  globalThis.fetch = impl as unknown as typeof fetch;
  return { calls };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  vi.stubGlobal(
    "requestAnimationFrame",
    (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0) as unknown as number,
  );
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.unstubAllGlobals();
});

async function renderDashboard() {
  const view = render(<RecommendationDemo />);
  expect(await screen.findByRole("button", { name: "Clear ratings" })).toBeVisible();
  return view;
}

describe("clearing every rating", () => {
  it("asks before it deletes, and deletes nothing if the answer is no", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi(() => jsonResponse({ cleared: 1 }));
    const { container } = await renderDashboard();

    await user.click(screen.getByRole("button", { name: "Clear ratings" }));
    const confirmation = screen.getByRole("group", {
      name: "Confirm clearing every rating",
    });
    expect(confirmation).toHaveTextContent("It cannot be undone from here.");
    // The commit is what focus lands on, so a keyboard viewer reads the
    // consequence before the key that would run it.
    expect(screen.getByRole("button", { name: "Clear all ratings" })).toHaveFocus();
    expect(await axe(container)).toHaveNoViolations();

    await user.click(screen.getByRole("button", { name: "Keep them" }));
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
    // Focus comes back to the control the viewer pressed, not to the document.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clear ratings" })).toHaveFocus(),
    );
  });

  it("cancels on Escape without deleting", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi(() => jsonResponse({ cleared: 1 }));
    await renderDashboard();

    await user.click(screen.getByRole("button", { name: "Clear ratings" }));
    await user.keyboard("{Escape}");

    expect(
      screen.queryByRole("group", { name: "Confirm clearing every rating" }),
    ).not.toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  });

  it("deletes once confirmed, and says so", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi(() => jsonResponse({ cleared: 1 }));
    await renderDashboard();

    await user.click(screen.getByRole("button", { name: "Clear ratings" }));
    await user.click(screen.getByRole("button", { name: "Clear all ratings" }));

    await waitFor(() =>
      expect(calls.filter((call) => call.method === "DELETE")).toHaveLength(1),
    );
    expect(
      await screen.findByText("Every rating for this persona was cleared."),
    ).toBeVisible();
    // The dashboard is re-read after the delete commits, never before.
    const order = calls.map((call) => `${call.method} ${new URL(call.url, "http://x").pathname}`);
    expect(order.at(-1)).toBe(`GET /api/users/${USER_ID}`);
  });

  it("keeps the offer open when the delete failed", async () => {
    const user = userEvent.setup();
    mockApi(() => jsonResponse({ detail: "Could not reset ratings" }, 502));
    await renderDashboard();

    await user.click(screen.getByRole("button", { name: "Clear ratings" }));
    await user.click(screen.getByRole("button", { name: "Clear all ratings" }));

    // The ratings are still there, so the offer to clear them is still true.
    expect(
      await screen.findByRole("group", { name: "Confirm clearing every rating" }),
    ).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not reset ratings");
    expect(
      screen.queryByText("Every rating for this persona was cleared."),
    ).not.toBeInTheDocument();
  });
});
