"use client";

import { useEffect } from "react";

import { ErrorState } from "@/components/ui/resource-states";

export default function ProductError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="app-page">
      <ErrorState label="This page" message="The route could not be rendered. Primary navigation is still available." />
    </div>
  );
}
