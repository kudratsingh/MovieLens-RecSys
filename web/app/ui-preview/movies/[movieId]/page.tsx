import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EvidenceDetails } from "@/components/discover/featured-movie";
import { MovieRail } from "@/components/movie/movie-rail";
import { PosterCard } from "@/components/movie/poster-card";
import { RatingControl } from "@/components/movie/rating-control";
import { StateControls } from "@/components/movie/state-controls";
import { Drawer } from "@/components/ui/drawer";
import { evidenceFixture, movies } from "@/lib/fixtures/movie-fixtures";
import type { MovieCard } from "@/lib/movie-types";
import "./movie-detail.css";

export const metadata: Metadata = { title: "Movie detail" };

export default async function MovieDetailPage({ params }: { params: Promise<{ movieId: string }> }) {
  const { movieId } = await params;
  const movie: MovieCard | undefined = movies.find((item) => item.id === Number(movieId));
  if (!movie) notFound();

  return (
    <div className="app-page movie-detail-page">
      <section className="movie-detail" aria-labelledby="movie-title">
        <div className="movie-detail-poster"><PosterCard movie={movie} priority /></div>
        <div className="movie-detail-copy">
          <p className="eyebrow">Movie detail · Recorded metadata</p>
          <h1 className="display-title" id="movie-title">{movie.title}</h1>
          <p className="movie-detail-meta">{movie.year ?? "Year unknown"} · {movie.genres.join(" · ") || "Genre unavailable"}</p>
          <p className="movie-overview">{movie.overview ?? "An overview is not available for this title."}</p>
          <StateControls initialState={movie.state} title={movie.title} />
          {movie.state.watched ? <RatingControl initialRating={movie.state.rating} title={movie.title} /> : null}
          <Drawer buttonLabel="Model details" eyebrow="Technical evidence" title="How this result was served">
            <EvidenceDetails evidence={evidenceFixture} reason={movie.reason} />
          </Drawer>
        </div>
      </section>
      <MovieRail movies={movies.filter((item) => item.id !== movie.id).slice(0, 6)} seeAllHref="/ui-preview/browse" title="Keep exploring" />
    </div>
  );
}
