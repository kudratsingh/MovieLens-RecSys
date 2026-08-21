import { PosterSkeleton } from "@/components/ui/resource-states";

export default function DiscoverLoading() {
  return (
    <div className="app-page">
      <p className="eyebrow">For you</p>
      <h1 className="section-title mt-3 mb-8">Finding a strong first pick…</h1>
      <PosterSkeleton count={5} />
    </div>
  );
}
