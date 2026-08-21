"use client";

import { useCallback, useEffect, useState } from "react";

import { PosterCard } from "@/components/movie/poster-card";
import { Icon } from "@/components/ui/icons";
import type { MovieCard } from "@/lib/movie-types";
import "./quick-pick-preview.css";

export function QuickPickPreview({ movie }: { movie: MovieCard }) {
  const [message, setMessage] = useState("Choose an action or use J, K, or L.");

  const choose = useCallback((action: "Not for me" | "Watchlist" | "Watched") => {
    setMessage(`${action} selected for ${movie.title}. Preview only; nothing was saved.`);
  }, [movie.title]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.key.toLowerCase() === "j") choose("Not for me");
      if (event.key.toLowerCase() === "k") choose("Watchlist");
      if (event.key.toLowerCase() === "l") choose("Watched");
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [choose]);

  return (
    <section className="quick-pick-card" aria-labelledby="quick-pick-title">
      <div className="quick-pick-poster"><PosterCard movie={movie} priority /></div>
      <div className="quick-pick-copy">
        <p className="eyebrow">1 of 5 · Recorded queue</p>
        <h1 className="display-title" id="quick-pick-title">{movie.title}</h1>
        <p>{movie.year} · {movie.genres.join(" · ")}</p>
        <p className="quick-pick-overview">{movie.overview}</p>
        <div className="quick-pick-actions">
          <button className="button-secondary" onClick={() => choose("Not for me")} type="button">Not for me <kbd>J</kbd></button>
          <button className="button-primary" onClick={() => choose("Watchlist")} type="button"><Icon name="bookmark" /> Watchlist <kbd>K</kbd></button>
          <button className="button-secondary" onClick={() => choose("Watched")} type="button"><Icon name="check" /> Watched <kbd>L</kbd></button>
        </div>
        <p aria-live="polite" className="quick-pick-message">{message}</p>
      </div>
    </section>
  );
}
