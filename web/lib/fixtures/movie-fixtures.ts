import type {
  EvidenceRecord,
  MovieCard,
  ResourceName,
  ResourceResult,
} from "@/lib/movie-types";

const state = (
  overrides: Partial<MovieCard["state"]> = {},
): MovieCard["state"] => ({
  watched: false,
  watchlisted: false,
  rating: null,
  suppressed: false,
  ...overrides,
});

export const movies = [
  {
    id: 101,
    title: "The Handmaiden",
    year: 2016,
    genres: ["Thriller", "Drama"],
    posterSrc: "/posters/handmaiden.svg",
    posterAlt: "Abstract red and cream poster for The Handmaiden",
    overview:
      "A con artist enters a secluded estate and discovers that every plan has another plan inside it.",
    reason: "Your history leans toward precise thrillers with morally tangled characters.",
    rank: 1,
    state: state({ watchlisted: true }),
  },
  {
    id: 102,
    title: "In the Mood for Love",
    year: 2000,
    genres: ["Romance", "Drama"],
    posterSrc: "/posters/in-the-mood.svg",
    posterAlt: "Abstract amber and plum poster for In the Mood for Love",
    overview: "Two neighbors form a quiet bond after making the same discovery.",
    reason: "A measured, character-first drama from a director you revisit.",
    rank: 2,
    state: state(),
  },
  {
    id: 103,
    title: "Memories of Murder",
    year: 2003,
    genres: ["Crime", "Mystery"],
    posterSrc: "/posters/memories.svg",
    posterAlt: "Abstract ochre landscape poster for Memories of Murder",
    overview: "Detectives chase a pattern through a rain-soaked rural province.",
    reason: "Strong overlap with the mysteries and crime films in this history.",
    rank: 3,
    state: state({ watched: true, rating: 4.5 }),
  },
  {
    id: 104,
    title: "Portrait of a Lady on Fire",
    year: 2019,
    genres: ["Drama", "Romance"],
    posterSrc: "/posters/portrait.svg",
    posterAlt: "Abstract cobalt and flame poster for Portrait of a Lady on Fire",
    overview: "A painter and her subject see each other with uncommon clarity.",
    reason: "Patient visual storytelling with a high affinity to recent ratings.",
    rank: 4,
    state: state({ watched: true, rating: 5 }),
  },
  {
    id: 105,
    title: "Perfect Blue",
    year: 1997,
    genres: ["Animation", "Thriller"],
    posterSrc: "/posters/perfect-blue.svg",
    posterAlt: "Abstract blue and crimson poster for Perfect Blue",
    overview: "A performer loses her footing between image, memory, and reality.",
    reason: "A sharper, stranger branch from your psychological-thriller history.",
    rank: 5,
    state: state(),
  },
  {
    id: 106,
    title: "Moonlight",
    year: 2016,
    genres: ["Drama"],
    posterSrc: "/posters/moonlight.svg",
    posterAlt: "Abstract moonlit blue portrait poster for Moonlight",
    overview: "Three chapters trace a young man becoming himself.",
    rank: 6,
    state: state({ watchlisted: true }),
  },
  {
    id: 107,
    title: "The Worst Person in the World",
    year: 2021,
    genres: ["Comedy", "Drama"],
    posterSrc: "/posters/worst-person.svg",
    posterAlt: "Abstract city-at-dawn poster for The Worst Person in the World",
    overview: "A restless search for a life that feels chosen rather than inherited.",
    rank: 7,
    state: state(),
  },
  {
    id: 108,
    title: "Burning",
    year: 2018,
    genres: ["Mystery", "Drama"],
    posterSrc: "/posters/burning.svg",
    posterAlt: "Abstract sunset poster for Burning",
    overview: "An old acquaintance, a new stranger, and a disappearance refuse to line up.",
    rank: 8,
    state: state({ watched: true, rating: 4 }),
  },
  {
    id: 109,
    title: "A Separation",
    year: 2011,
    genres: ["Drama"],
    posterSrc: null,
    posterAlt: "",
    overview: "One family decision opens a knot of obligation and truth.",
    rank: 9,
    state: state(),
  },
  {
    id: 110,
    title: "Decision to Leave",
    year: 2022,
    genres: ["Mystery", "Romance"],
    posterSrc: "/posters/decision.svg",
    posterAlt: "Abstract teal mountain poster for Decision to Leave",
    overview: "A detective finds suspicion and longing difficult to separate.",
    rank: 10,
    state: state({ watchlisted: true }),
  },
] as const satisfies readonly MovieCard[];

export const evidenceFixture: EvidenceRecord = {
  policy: "item-item candidates → learned ranker",
  modelVersion: "lgbm-ranker-2026.08",
  candidateVersion: "item-item-v3",
  featureVersion: "online-features-v2",
  requestId: "req_demo_7f31b2",
  latencyMs: 42,
  fallbackReason: null,
};

export function recordedResource<T>(
  name: ResourceName,
  data: T,
  failedResources: readonly string[] = [],
): ResourceResult<T> {
  if (failedResources.includes(name)) {
    return {
      status: "error",
      message: `${name[0].toUpperCase()}${name.slice(1)} could not be loaded. Other sections remain available.`,
      source: "recorded-contract-fixture",
    };
  }

  return { status: "ready", data, source: "recorded-contract-fixture" };
}

export function fixtureFailures(value: string | string[] | undefined): string[] {
  const source = Array.isArray(value) ? value.join(",") : (value ?? "");
  return source
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
