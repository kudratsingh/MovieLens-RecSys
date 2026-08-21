import type { Metadata } from "next";

import { QuickPickPreview } from "@/components/quick-picks/quick-pick-preview";
import { movies } from "@/lib/fixtures/movie-fixtures";

export const metadata: Metadata = { title: "Quick picks" };

export default function QuickPicksPage() {
  return (
    <div className="app-page">
      <QuickPickPreview movie={movies[4]} />
    </div>
  );
}
