/**
 * What Discover says after a committed feedback action.
 *
 * The four states are different things to the deployed system and the copy has
 * to keep them apart (ADR 0012 §4): a watchlist entry is organisational, a
 * dismissal is an undoable exclusion rather than a negative label, and deleting
 * a rating leaves the watched interaction behind. Movie detail and Library
 * phrase the same events for their own routes; Bundle 7 converges the controls
 * and can converge this with them.
 */

import type { MovieStateResource } from "@/lib/movie-state/mutate";

export function discoverMutationAnnouncement(
  resource: MovieStateResource,
  method: "PUT" | "DELETE",
  title: string,
): string {
  if (resource === "rating") {
    return method === "PUT"
      ? `Rating saved for ${title}.`
      : `Rating removed from ${title}; watched history was preserved.`;
  }
  if (resource === "watched") {
    return method === "PUT"
      ? `${title} marked watched.`
      : `${title} removed from watched history.`;
  }
  if (resource === "watchlist") {
    return method === "PUT"
      ? `${title} saved to watchlist.`
      : `${title} removed from watchlist.`;
  }
  return method === "PUT"
    ? `${title} will be excluded from recommendations.`
    : `${title} is eligible again.`;
}
