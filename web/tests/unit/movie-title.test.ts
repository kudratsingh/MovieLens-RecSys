/**
 * The two rules every movie surface shares.
 *
 * Table-driven on purpose: the defects these replace were all the same shape —
 * one route applying the rule and another not — so the interesting assertion is
 * that a case list holds for the single implementation, not that one component
 * happens to render one title correctly.
 */

import { describe, expect, it } from "vitest";

import { displayTitle, posterInitials } from "@/lib/movie-types";

describe("displayTitle", () => {
  const cases: [string, number | null, string][] = [
    // The ordinary MovieLens shape: the year is also structured, so it goes.
    ["Toy Story (1995)", 1995, "Toy Story"],
    ["Babe (1995)", 1995, "Babe"],
    ["Sense and Sensibility (1995)", 1995, "Sense and Sensibility"],
    // A parenthetical that disagrees with the structured year is part of the
    // name — a re-release, a remake marker — and is left alone.
    ["Se7en (1995)", 1997, "Se7en (1995)"],
    // Nothing structured to compare against: keep what the catalog holds.
    ["Heat (1995)", null, "Heat (1995)"],
    // A year inside the title rather than trailing it stays put.
    ["2001: A Space Odyssey", 1968, "2001: A Space Odyssey"],
    ["Blade Runner 2049 (2017)", 2017, "Blade Runner 2049"],
  ];

  it.each(cases)("%s / %s → %s", (title, year, expected) => {
    expect(displayTitle(title, year)).toBe(expected);
  });
});

describe("posterInitials", () => {
  const cases: [string, string][] = [
    ["Sense and Sensibility", "SS"],
    ["The Handmaiden", "H"],
    ["Memories of Murder", "MM"],
    // The stop-word list is closed at `the a an of in to and`. "for" is not on
    // it, so this is "MF" — pinned here so the list cannot drift by accident.
    ["In the Mood for Love", "MF"],
    // One word yields one letter: "Ba" reads as a truncation, "B" as a mark.
    ["Babe", "B"],
    ["2001: A Space Odyssey", "2S"],
    ["Action Fan", "AF"],
    // Nothing survives the rule: an empty title, or only stop words.
    ["", "?"],
    ["The A", "?"],
    // Punctuation is not a word — this is the case that produced `B(` when the
    // raw MovieLens title reached the mark instead of the display title.
    ["Babe (1995)", "B"],
    ["— (2019)", "?"],
  ];

  it.each(cases)("%s → %s", (title, expected) => {
    expect(posterInitials(title)).toBe(expected);
  });

  it("never emits a bracket or a lowercase glyph", () => {
    const titles = [
      "the lord of the rings",
      "Sense and Sensibility (1995)",
      "(500) Days of Summer",
      "Amélie",
    ];

    for (const title of titles) {
      const mark = posterInitials(title);
      expect(mark).toBe(mark.toUpperCase());
      expect(mark).not.toMatch(/[()[\]{}]/);
      expect(mark.length).toBeLessThanOrEqual(2);
    }
  });
});
