import { describe, expect, it } from "vitest";

import type { MovieStateAction } from "@/lib/movie-state/actions";
import { movieStateAnnouncement } from "@/lib/movie-state/announce";
import { failureState } from "@/lib/resources/state";

function committed(action: MovieStateAction) {
  return { kind: "committed" as const, action };
}

const RATE: MovieStateAction = { resource: "rating", method: "PUT", rating: 4 };
const UNRATE: MovieStateAction = { resource: "rating", method: "DELETE" };
const DISMISS: MovieStateAction = { resource: "dismissal", method: "PUT" };
const SAVE: MovieStateAction = { resource: "watchlist", method: "PUT" };

describe("every voice keeps the four states' meanings distinct", () => {
  it("says what a rating deletion does and does not do", () => {
    expect(
      movieStateAnnouncement(committed(UNRATE), { title: "Heat", voice: "discover" }),
    ).toContain("watched history was preserved");
    expect(
      movieStateAnnouncement(committed(UNRATE), { title: "Heat", voice: "detail" }),
    ).toContain("stays in watched history");
    expect(
      movieStateAnnouncement(committed(UNRATE), {
        title: "Heat",
        voice: "library",
        persona: "Action Fan",
      }),
    ).toContain("still watched history");
  });

  it("describes dismissal as an exclusion rather than a dislike", () => {
    for (const voice of ["discover", "detail", "library"] as const) {
      const line = movieStateAnnouncement(committed(DISMISS), {
        title: "Heat",
        voice,
        persona: "Action Fan",
      });
      expect(line).toMatch(/exclud/i);
      expect(line).not.toMatch(/dislike|negative|never recommend/i);
    }
  });

  it("keeps watchlist organizational and free of any model claim", () => {
    expect(
      movieStateAnnouncement(committed(SAVE), { title: "Heat", voice: "discover" }),
    ).toBe("Heat saved to watchlist.");
    expect(
      movieStateAnnouncement(committed(SAVE), { title: "Heat", voice: "detail" }),
    ).toContain("changes no recommendation input");
    expect(
      movieStateAnnouncement(committed(SAVE), {
        title: "Heat",
        voice: "library",
        persona: "Action Fan",
      }),
    ).toContain("Saving does not change recommendations");
  });

  it("leads a saved rating with the phrase each journey waits on", () => {
    expect(
      movieStateAnnouncement(committed(RATE), { title: "Heat", voice: "discover" }),
    ).toBe("Rating saved for Heat.");
    expect(
      movieStateAnnouncement(committed(RATE), { title: "Heat", voice: "detail" }),
    ).toMatch(/^Rating saved\. 4 stars for Heat/);
    expect(
      movieStateAnnouncement(committed(RATE), {
        title: "Heat",
        voice: "library",
        persona: "Action Fan",
      }),
    ).toMatch(/^Rating saved for Heat in Action Fan's library\./);
  });

  it("never presents a star value as a graded model signal", () => {
    for (const voice of ["detail", "library"] as const) {
      expect(
        movieStateAnnouncement(committed(RATE), {
          title: "Heat",
          voice,
          persona: "Action Fan",
        }),
      ).toMatch(/display feedback|not a graded/i);
    }
  });
});

describe("a conflict reads as a correction rather than a dead end", () => {
  it("tells a card and a detail page that the current state has been loaded", () => {
    for (const voice of ["discover", "detail"] as const) {
      expect(
        movieStateAnnouncement({ kind: "conflict" }, { title: "Heat", voice }),
      ).toContain("Its current state has been loaded");
    }
  });

  it("names the persona whose record is being shown in the Library", () => {
    expect(
      movieStateAnnouncement(
        { kind: "conflict" },
        { title: "Heat", voice: "library", persona: "Action Fan" },
      ),
    ).toBe("Heat changed elsewhere. Action Fan's latest saved state is shown.");
  });
});

describe("a failed write says what was not saved", () => {
  const failure = failureState({
    status: "upstream-error",
    resource: "movie-detail",
    reason: "server",
    requestId: "req-1",
  });

  it("names the cause where the surface has room for it", () => {
    expect(
      movieStateAnnouncement({ kind: "failed", failure }, { title: "Heat", voice: "detail" }),
    ).toBe("The recommendation API returned an error. Nothing was saved for Heat.");
  });

  it("hands a card the correlation ID instead", () => {
    expect(
      movieStateAnnouncement(
        { kind: "failed", failure },
        { title: "Heat", voice: "discover" },
      ),
    ).toBe("Heat was not saved. It was left as it was. Request req-1.");
  });

  it("tells a Library reader the collection was put back", () => {
    expect(
      movieStateAnnouncement(
        { kind: "failed", failure },
        { title: "Heat", voice: "library", persona: "Action Fan" },
      ),
    ).toBe("Could not update Heat. Action Fan's saved state was restored.");
  });

  it("routes an expired session to reauthentication rather than a retry", () => {
    const expired = failureState({
      status: "auth-expired",
      resource: "movie-detail",
      reason: "session-expired",
      requestId: "req-2",
    });

    expect(
      movieStateAnnouncement({ kind: "failed", failure: expired }, {
        title: "Heat",
        voice: "detail",
      }),
    ).toContain("Sign in again");
  });
});
