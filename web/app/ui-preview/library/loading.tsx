import { PosterSkeleton } from "@/components/ui/resource-states";

export default function LibraryLoading() {
  return (
    <div className="app-page">
      <p className="eyebrow">Library</p>
      <h1 className="section-title mt-3 mb-8">Gathering your movie history…</h1>
      <PosterSkeleton count={5} />
    </div>
  );
}
