import type { TasteSummaryResponse } from "@/lib/api";
import { formatLibraryDate } from "@/lib/library/collection";

/**
 * A readable outline of the ratings that are stored right now.
 *
 * The wording is the point of this component. The summary is recomputed from
 * the current projection on every read, which makes it honest about the
 * library and silent about the model: it is not the deployed ranker's
 * explanation, it is not a Feast feature snapshot, and it does not describe how
 * a recommendation was scored. The API says so itself in `explanation`, and its
 * `live-ratings-v1` source is shown rather than paraphrased.
 */
export function TasteSummary({ summary }: { summary: TasteSummaryResponse }) {
  return (
    <div className="taste-summary">
      <div className="taste-summary-copy">
        <p className="eyebrow">Live ratings summary</p>
        <h2 className="section-title" id="taste-summary-title">
          A readable outline of this persona&apos;s ratings.
        </h2>
        <p className="muted">{summary.explanation}</p>
        <p className="taste-summary-meta">
          Recomputed from {summary.rating_count}{" "}
          {summary.rating_count === 1 ? "rating" : "ratings"} on every read
          {summary.average_rating === null
            ? ""
            : ` · ${summary.average_rating.toFixed(1)} average`}{" "}
          · source <code>{summary.source}</code> · read{" "}
          {formatLibraryDate(summary.generated_at)}
        </p>
      </div>

      {summary.top_genres.length ? (
        <ul className="taste-genres">
          {summary.top_genres.map((genre) => (
            <li className="taste-genre" key={genre.genre}>
              <span>{genre.genre}</span>
              <span className="taste-genre-figures">
                {genre.rated_count} rated · {genre.average_rating.toFixed(1)} average
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Rate a movie to reveal this summary.</p>
      )}
    </div>
  );
}
