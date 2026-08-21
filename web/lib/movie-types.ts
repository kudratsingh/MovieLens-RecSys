export type MovieState = {
  watched: boolean;
  watchlisted: boolean;
  rating: number | null;
  suppressed: boolean;
};

export type MovieCard = {
  id: number;
  title: string;
  year: number | null;
  genres: readonly string[];
  posterSrc: string | null;
  posterAlt: string;
  overview: string | null;
  reason?: string;
  rank?: number;
  state: MovieState;
};

export type EvidenceRecord = {
  policy: string;
  modelVersion: string;
  candidateVersion: string;
  featureVersion: string;
  requestId: string;
  latencyMs: number;
  fallbackReason: string | null;
};

export type ResourceName =
  | "recommendations"
  | "catalog"
  | "library"
  | "evidence";

export type ResourceResult<T> =
  | { status: "ready"; data: T; source: "recorded-contract-fixture" }
  | { status: "error"; message: string; source: "recorded-contract-fixture" };
