import { PosterCard } from "@/components/movie/poster-card";
import type { MovieCard } from "@/lib/movie-types";
import "./movie-collection.css";

export function MovieCollection({
  movies,
  label,
  compact = false,
}: {
  movies: readonly MovieCard[];
  label: string;
  compact?: boolean;
}) {
  return (
    <div aria-label={label} className={`movie-grid ${compact ? "movie-grid-compact" : ""}`} role="list">
      {movies.map((movie, index) => (
        <div key={movie.id} role="listitem">
          <PosterCard density={compact ? "compact" : "standard"} movie={movie} priority={index === 0} />
        </div>
      ))}
    </div>
  );
}
