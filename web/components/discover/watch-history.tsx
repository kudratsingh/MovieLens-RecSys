import Link from "next/link";

import { ResourceRegion } from "@/components/ui/resource-region";
import { EmptyState } from "@/components/ui/resource-states";
import type { HistoryResponse } from "@/lib/api";
import type { ResourceState } from "@/lib/resources/state";
import "./discover.css";

/**
 * Watch history as its own region.
 *
 * It explains why the ranked list looks the way it does, which makes it worth
 * showing — and makes it supporting context rather than the decision. It loads
 * separately from recommendations, so when it fails the movie above it is
 * untouched, and it renders without client JavaScript.
 */
export function WatchHistory({
  state,
  personaName,
  browseHref,
}: {
  state: ResourceState<HistoryResponse>;
  personaName: string;
  browseHref: string;
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
                <span className="discover-history-title">{item.title}</span>
                <span className="discover-history-meta">
                  {item.genres.join(" · ") || "Unclassified"}
                  {item.rating === null ? "" : ` · rated ${item.rating}`}
                </span>
              </li>
            ))}
          </ol>
        )}
      </ResourceRegion>
    </section>
  );
}
