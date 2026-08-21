"use client";

import { useState } from "react";

import type { FeedbackMutationResponse, MovieState } from "@/lib/api";

export function MovieDetailActions({
  movieId,
  title,
  userId,
  initialState,
}: {
  movieId: number;
  title: string;
  userId: number;
  initialState: MovieState | null;
}) {
  const [movieState, setMovieState] = useState(initialState);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const rating = movieState?.rating ?? null;

  async function mutate(method: "PUT" | "DELETE", nextRating?: number) {
    setSaving(true);
    setMessage(null);
    try {
      const response = await fetch(
        `/api/users/${userId}/movies/${movieId}/rating` +
          `?expected_revision=${movieState?.revision ?? 0}`,
        {
          method,
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
            "x-csrf-token": await csrfToken(),
          },
          body: method === "PUT" ? JSON.stringify({ rating: nextRating }) : undefined,
        },
      );
      const payload = (await response.json()) as
        | FeedbackMutationResponse
        | { detail?: string };
      if (!response.ok || !isMutationResponse(payload)) {
        throw new Error(
          "detail" in payload && payload.detail
            ? payload.detail
            : "Could not save rating",
        );
      }
      setMovieState(payload.state);
      setMessage(
        method === "PUT"
          ? "Rating saved. This title now counts as watched history."
          : "Rating removed. Watched history is preserved.",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save rating");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="mt-8 border-t border-white/10 pt-6" aria-labelledby="rating-heading">
      <h2 className="text-sm font-semibold" id="rating-heading">Rate {title}</h2>
      <p className="mt-2 text-sm leading-6 text-zinc-500">
        A rating records watched history and refreshes unseen recommendations. Star
        magnitude is not yet an immediate model-training signal.
      </p>
      <div className="mt-4 flex gap-2" aria-label={`Rate ${title}`}>
        {[1, 2, 3, 4, 5].map((value) => (
          <button
            aria-label={`${value} stars`}
            className={`grid size-10 place-items-center rounded-lg border text-sm font-semibold transition ${
              rating === value
                ? "border-amber-300 bg-amber-300 text-zinc-950"
                : "border-white/10 text-zinc-400 hover:border-amber-300/50 hover:text-amber-200"
            }`}
            disabled={saving}
            key={value}
            onClick={() => void mutate("PUT", value)}
            type="button"
          >
            {value}
          </button>
        ))}
        {rating !== null ? (
          <button
            className="ml-2 rounded-lg border border-white/10 px-3 text-xs text-zinc-400 transition hover:border-white/25 hover:text-white"
            disabled={saving}
            onClick={() => void mutate("DELETE")}
            type="button"
          >
            Clear
          </button>
        ) : null}
      </div>
      <p className="mt-3 min-h-5 text-xs text-zinc-400" role="status">{message}</p>
    </section>
  );
}

function isMutationResponse(value: unknown): value is FeedbackMutationResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<FeedbackMutationResponse>;
  return typeof candidate.request_id === "string" && typeof candidate.state?.revision === "number";
}

async function csrfToken(): Promise<string> {
  const response = await fetch("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not create a secure feedback request");
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}
