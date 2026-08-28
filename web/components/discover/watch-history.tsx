import Image from "next/image";
import Link from "next/link";

import { PosterFallbackMark } from "@/components/movie/poster-card";
import { ResourceRegion } from "@/components/ui/resource-region";
import { EmptyState } from "@/components/ui/resource-states";
import type { HistoryItem, HistoryResponse } from "@/lib/api";
import { displayTitle } from "@/lib/movie-types";
import type { ResourceState } from "@/lib/resources/state";
import "./discover.css";

/**
 * Watch history as its own region.
 *
 * It explains why the ranked list looks the way it does, which makes it worth
 * showing — and makes it supporting context rather than the decision. It loads
 * separately from recommendations, so when it fails the movie above it is
 * untouched, and it renders without client JavaScript.
 *
 * Each row is a link, and it carries artwork. Both were missing while the
 * payload carried neither a poster nor a structured year: the region printed
 * four dead lines in the middle of a poster-first product, and a viewer who
 * recognised a title in it had no way to open it. The read model carries both
 * fields now, so the row uses the same primitives every other movie surface
 * does rather than a treatment of its own.
 */
export function WatchHistory({
  state,
  personaName,
  browseHref,
  movieHref,
}: {
  state: ResourceState<HistoryResponse>;
  personaName: string;
  browseHref: string;
  /** Owned by the route, so a return address travels with the link. */
  movieHref: (movieId: number) => string;
}) {
  return (
    <section aria-labelledby="history-heading" className="discover-history">
      <p className="eyebrow">Signals behind this list</p>
      <h2 className="section-title" id="history-heading">
        What {personaName} has watched
      </h2>
      <ResourceRegion
        empty={
          <EmptyState
            action={
              <Link className="button-quiet" href={browseHref}>
                Browse the catalog
              </Link>
            }
            message="Nothing is recorded yet, so the router is serving the tenant-wide popularity fallback."
            title="No watch history yet"
          />
        }
        label="Watch history"
        state={state}
      >
        {(data) => (
          <ol>
            {data.items.map((item) => (
              <li key={`${item.movie_id}-${item.timestamp}`}>
                <HistoryRow href={movieHref(item.movie_id)} item={item} />
              </li>
            ))}
          </ol>
        )}
      </ResourceRegion>
    </section>
  );
}

/**
 * One watched title.
 *
 * The whole row is a single link for the reason a poster card is: two anchors
 * to the same movie cost a keyboard viewer an extra stop per row and announce
 * the same destination twice. The artwork is decorative because the title it
 * sits beside is the link's name — a second announcement of the same film, or
 * of "Artwork unavailable" four times down a list, is noise.
 */
function HistoryRow({ href, item }: { href: string; item: HistoryItem }) {
  // Coerced rather than trusted: the API and the web app deploy as separate
  // images, so a backend that predates these fields sends no key at all behind
  // a type that promises `number | null`.
  const year = item.release_year ?? null;
  const poster = item.poster_url ?? null;
  const name = displayTitle(item.title, year);

  return (
    <Link className="discover-history-row" href={href}>
      <span aria-hidden="true" className="discover-history-thumb">
        {poster ? (
          <Image alt="" fill sizes="48px" src={poster} />
        ) : (
          <PosterFallbackMark title={name} />
        )}
      </span>
      <span className="discover-history-copy">
        <span className="discover-history-title">{name}</span>
        <span className="discover-history-meta">{metaLine(item, year)}</span>
      </span>
    </Link>
  );
}

/**
 * The year moved onto this line when the payload started carrying it, for the
 * reason `displayTitle` exists: printing "Heat (1995)" above a line that also
 * says 1995 is the duplication that rule removes.
 */
function metaLine(item: HistoryItem, year: number | null): string {
  const parts = [
    typeof year === "number" && Number.isFinite(year) ? String(year) : null,
    item.genres.length ? item.genres.join(" · ") : "Unclassified",
    // Displayed, not weighted: under ADR 0012 the deployed path counts a rating
    // as one observed watch, whatever its magnitude.
    item.rating === null ? null : `rated ${item.rating}`,
  ];
  return parts.filter((part): part is string => part !== null).join(" · ");
}
