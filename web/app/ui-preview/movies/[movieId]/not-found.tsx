import Link from "next/link";

import { EmptyState } from "@/components/ui/resource-states";

export default function MovieNotFound() {
  return (
    <div className="app-page">
      <EmptyState
        action={<Link className="button-primary" href="/ui-preview/browse">Browse movies</Link>}
        message="This recorded fixture does not include that title."
        title="Movie not found"
      />
    </div>
  );
}
