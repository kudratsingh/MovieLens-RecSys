import { describe, expect, it } from "vitest";

import {
  personInitials,
  ratingValueText,
  runtimeText,
  tmdbScoreText,
  trailerEmbedUrl,
} from "@/lib/movie-details";

describe("runtimeText", () => {
  it("reads a runtime the way a person says it", () => {
    expect(runtimeText(145)).toBe("2h 25m");
    expect(runtimeText(120)).toBe("2h");
    expect(runtimeText(48)).toBe("48m");
  });

  it("says nothing rather than something false", () => {
    // No runtime, a zero, or a negative are all "we do not know", and a meta
    // line reading "0m" beside a feature film is worse than a shorter line.
    for (const value of [null, undefined, 0, -12, Number.NaN]) {
      expect(runtimeText(value)).toBeNull();
    }
  });
});

describe("tmdbScoreText", () => {
  it("carries the vote count with the average", () => {
    // An 8.4 from nine people and an 8.4 from nine thousand are not the same
    // claim, so the count is never dropped.
    expect(tmdbScoreText({ average: 7.84, count: 4812 })).toBe("7.8 / 10 · 4,812 ratings");
    expect(tmdbScoreText({ average: 9, count: 1 })).toBe("9.0 / 10 · 1 rating");
  });

  it("treats a score nobody gave as no score", () => {
    expect(tmdbScoreText({ average: 0, count: 0 })).toBeNull();
    expect(tmdbScoreText(null)).toBeNull();
  });
});

describe("trailerEmbedUrl", () => {
  it("builds the privacy-enhanced embed and escapes the key", () => {
    expect(trailerEmbedUrl({ provider: "youtube", key: "T7kfW4trvUM", name: "Trailer" })).toBe(
      "https://www.youtube-nocookie.com/embed/T7kfW4trvUM?autoplay=1&rel=0",
    );
    // The key reaches a URL, so it is escaped here even though it is validated
    // where it is written.
    expect(
      trailerEmbedUrl({ provider: "youtube", key: "a b?x=1", name: "Trailer" }),
    ).toContain("/embed/a%20b%3Fx%3D1?");
  });
});

describe("personInitials", () => {
  it("takes the first and last initial of a name", () => {
    expect(personInitials("Kim Min-hee")).toBe("KM");
    expect(personInitials("Céline Sciamma")).toBe("CS");
    expect(personInitials("Park Chan-wook Jr.")).toBe("PJ");
  });

  it("gives a mononym one letter rather than two from the same word", () => {
    expect(personInitials("Bono")).toBe("B");
    expect(personInitials("  ")).toBe("?");
  });
});

describe("ratingValueText", () => {
  it("prints a whole star without a trailing zero", () => {
    expect(ratingValueText(4)).toBe("4");
    // Half stars arrive from the Library's editor and have to read back here.
    expect(ratingValueText(4.5)).toBe("4.5");
  });
});
