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
    // Discover records a star from the follow-up panel, on a title it has
    // already marked watched, so its line answers the question the panel
    // leaves behind rather than stopping at "saved".
    expect(
      movieStateAnnouncement(committed(RATE), { title: "Heat", voice: "discover" }),
    ).toBe("Rated Heat 4/5. Ratings do not reorder the list — the watch already counts.");
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
    // Discover says it in the shortest form a card has room for: the watch is
    // the signal that reaches the model, and the star is not.
    expect(
      movieStateAnnouncement(committed(RATE), { title: "Heat", voice: "discover" }),
    ).toMatch(/do not reorder the list/);
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

describe("a refused transition states the rule instead of blaming a race", () => {
  // One of the rules ADR 0012's transition table states. The API sends the
  // sentence; nothing here parses it, and it is repeated rather than rewritten.
  const RULE = "a watched movie cannot be added to the watchlist";

  it("repeats the API's own sentence rather than translating it", () => {
    for (const voice of ["discover", "detail"] as const) {
      const line = movieStateAnnouncement(
        { kind: "refused", detail: RULE },
        { title: "Heat", voice },
      );
      expect(line).toBe(`Heat was not changed. ${RULE}.`);
      // The conflict copy would have been untrue here: nothing changed
      // anywhere, and a second press cannot succeed.
      expect(line).not.toContain("changed somewhere else");
      expect(line).not.toContain("try again");
    }
  });

  it("names the persona whose record was left alone in the Library", () => {
    expect(
      movieStateAnnouncement(
        { kind: "refused", detail: RULE },
        { title: "Heat", voice: "library", persona: "Action Fan" },
      ),
    ).toBe(`Heat was left as it is in Action Fan's library. ${RULE}.`);
  });

  it("does not double the terminator on a detail that already has one", () => {
    expect(
      movieStateAnnouncement(
        { kind: "refused", detail: "Undo the dismissal first." },
        { title: "Heat", voice: "detail" },
      ),
    ).toBe("Heat was not changed. Undo the dismissal first.");
  });

  it("says the control moved when the re-read actually moved it", () => {
    // The refusal triggers a canonical re-read, so the card can look different
    // a moment later. "Not changed" followed by a silent change is the version
    // of this that lies, and a reader who cannot see the control has no other
    // way to learn it moved.
    for (const voice of ["discover", "detail"] as const) {
      expect(
        movieStateAnnouncement(
          { kind: "refused", detail: RULE, corrected: true },
          { title: "Heat", voice },
        ),
      ).toBe(`Heat was not changed. ${RULE}. Its current state is shown.`);
    }
    expect(
      movieStateAnnouncement(
        { kind: "refused", detail: RULE, corrected: true },
        { title: "Heat", voice: "library", persona: "Action Fan" },
      ),
    ).toBe(`Heat was left as it is in Action Fan's library. ${RULE}. Its current state is shown.`);
  });

  it("claims no correction when the record confirmed what was on screen", () => {
    expect(
      movieStateAnnouncement(
        { kind: "refused", detail: RULE, corrected: false },
        { title: "Heat", voice: "detail" },
      ),
    ).toBe(`Heat was not changed. ${RULE}.`);
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
