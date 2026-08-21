import { PosterSkeleton } from "@/components/ui/resource-states";

export default function BrowseLoading() {
  return (
    <div className="app-page">
      <p className="eyebrow">Browse the shelves</p>
      <h1 className="section-title mt-3 mb-8">Opening the catalog…</h1>
      <PosterSkeleton count={6} />
    </div>
  );
}
