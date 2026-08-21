import type { Metadata } from "next";

import { FeaturedMovie } from "@/components/discover/featured-movie";
import { MovieRail } from "@/components/movie/movie-rail";
import { ErrorState } from "@/components/ui/resource-states";
import { evidenceFixture, fixtureFailures, movies, recordedResource } from "@/lib/fixtures/movie-fixtures";

export const metadata: Metadata = { title: "For you" };

export default async function DiscoverPage({
  searchParams,
}: {
  searchParams: Promise<{ fail?: string | string[] }>;
}) {
  const failed = fixtureFailures((await searchParams).fail);
  const recommendations = recordedResource("recommendations", movies, failed);
  const evidence = recordedResource("evidence", evidenceFixture, failed);

  return (
    <div className="app-page">
      {recommendations.status === "error" ? (
        <>
          <header className="mb-8 max-w-2xl">
            <p className="eyebrow">For you</p>
            <h1 className="display-title mt-3">A better movie night starts here.</h1>
          </header>
          <ErrorState label="Recommendations" message={recommendations.message} />
        </>
      ) : (
        <>
          <FeaturedMovie
            evidence={evidence.status === "ready" ? evidence.data : undefined}
            movie={recommendations.data[0]}
          />
          {evidence.status === "error" ? (
            <div className="mt-8">
              <ErrorState label="Technical evidence" message={evidence.message} />
            </div>
          ) : null}
          <MovieRail
            eyebrow="Ranked for Action Fan"
            movies={recommendations.data.slice(1)}
            seeAllHref="/ui-preview/browse?source=ranked"
            title="More worth a look"
          />
        </>
      )}
    </div>
  );
}
