import { fireEvent, render, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { LibraryRow } from "@/components/library/library-row";
import type { LibraryMovie, MovieState } from "@/lib/api";
import type { LibraryTab } from "@/lib/library/url-state";
import { posterInitials } from "@/lib/movie-types";

/**
 * What a Library row shows for one movie.
 *
 * The row is where two shared rules meet the payload that finally carries the
 * fields they need: the poster treatment every other surface already had, and
 * the one display title. Both are asserted here against the *rendered* row
 * rather than against the mappers, because the bug they close was a surface
 * printing the raw catalog string beside a structured year.
 */

const WATCHED_AT = "2026-08-16T21:05:00Z";

function state(overrides: Partial<MovieState> = {}): MovieState {
  return {
    dismissed_at: null,
    movie_id: 103,
    rating: 4.5,
    rating_updated_at: WATCHED_AT,
    revision: 7,
    tenant_id: "demo",
    updated_at: WATCHED_AT,
    user_id: 900000101,
    watched_at: WATCHED_AT,
    watchlisted_at: null,
    ...overrides,
  };
}

function movie(overrides: Partial<LibraryMovie> = {}): LibraryMovie {
  return {
    genres: ["Crime", "Mystery"],
    movie_id: 103,
    poster_url: null,
    release_year: 2003,
    state: state(),
    title: "Memories of Murder (2003)",
    ...overrides,
  };
}

function renderRow(overrides: Partial<LibraryMovie> = {}, tab: LibraryTab = "rated") {
  const onAction = vi.fn();
  const result = render(
    <ul>
      <LibraryRow
        busy={false}
        disabled={false}
        href="/movies/103"
        movie={movie(overrides)}
        onAction={onAction}
        persona="Action Fan"
        tab={tab}
      />
    </ul>,
  );
  return { ...result, onAction };
}

describe("a Library row names its movie once", () => {
  it("strips the year the metadata line already carries", () => {
    renderRow();

    expect(screen.getByRole("link", { name: "Memories of Murder" })).toHaveAttribute(
      "href",
      "/movies/103",
    );
    expect(screen.queryByText(/Memories of Murder \(2003\)/)).not.toBeInTheDocument();
    expect(screen.getByText("2003 · Crime · Mystery")).toBeVisible();
  });

  it("keeps a trailing parenthetical that is not the structured year", () => {
    // A re-release marker or a disambiguator is part of the name; only the year
    // the row is about to print on its own line is redundant.
    renderRow({ title: "Se7en (1995)", release_year: 1997 });

    expect(screen.getByRole("link", { name: "Se7en (1995)" })).toBeVisible();
  });

  it("says what it does not know rather than printing an empty line", () => {
    renderRow({ genres: [], release_year: null, title: "Untitled" });

    expect(screen.getByText("Genres unavailable")).toBeVisible();
  });

  it("survives a backend that predates the field rather than printing undefined", () => {
    // The API and the web app are separate images and deploy independently, so
    // a running backend can answer with no `poster_url`/`release_year` key at
    // all — behind a generated type that promises both. The narrow response
    // validator does not check them, so the row is where this has to hold.
    const stale = movie();
    delete (stale as Partial<LibraryMovie>).release_year;
    delete (stale as Partial<LibraryMovie>).poster_url;
    render(
      <ul>
        <LibraryRow
          busy={false}
          disabled={false}
          href="/movies/103"
          movie={stale}
          onAction={vi.fn()}
          persona="Action Fan"
          tab="rated"
        />
      </ul>,
    );

    expect(screen.getByText("Crime · Mystery")).toBeVisible();
    expect(screen.queryByText(/undefined/)).not.toBeInTheDocument();
    // No structured year to compare against, so the title keeps its own.
    expect(screen.getByRole("link", { name: "Memories of Murder (2003)" })).toBeVisible();
    expect(screen.getByTestId("poster-fallback")).toBeInTheDocument();
  });

  it("uses the display title in the control labels and the removal consequence", () => {
    renderRow({ state: state({ rating: null }) }, "history");

    fireEvent.click(screen.getByRole("button", { name: "Remove from history" }));

    const confirm = screen.getByRole("group", {
      name: "Confirm removing Memories of Murder from watched history",
    });
    expect(within(confirm).getByText(/Removing Memories of Murder from history/)).toBeVisible();
  });
});

describe("a Library row carries the artwork the payload now sends", () => {
  it("renders the poster, decoratively, beside the title that names it", async () => {
    const { container } = renderRow({ poster_url: "/posters/memories.svg" });

    const poster = container.querySelector("img");
    expect(poster).toBeInTheDocument();
    // The title beside it is the accessible name; a second announcement of the
    // same movie twelve rows down a list is noise.
    expect(poster).toHaveAttribute("alt", "");
    expect(container.querySelector(".library-thumb")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.queryByTestId("poster-fallback")).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("shows the one shared mark when there is no poster", () => {
    renderRow({ title: "Sense and Sensibility (1995)", release_year: 1995 });

    const mark = screen.getByTestId("poster-fallback");
    // Derived from the display title, so the mark is the same "SS" the same
    // movie shows on Discover, Browse, detail, and Quick Picks — not the "S("
    // the raw MovieLens title produces.
    expect(within(mark).getByText("SS")).toBeInTheDocument();
    expect(within(mark).getByText("SS")).toHaveTextContent(
      posterInitials("Sense and Sensibility"),
    );
  });

  it("falls back rather than leaving a broken frame when the poster 404s", () => {
    const { container } = renderRow({ poster_url: "/posters/gone.svg" });

    fireEvent.error(container.querySelector("img")!);

    expect(screen.getByTestId("poster-fallback")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Memories of Murder" })).toBeVisible();
  });
});
