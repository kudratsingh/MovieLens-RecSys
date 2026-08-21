"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function MovieDetailError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Movie detail failed", error);
  }, [error]);

  return (
    <main className="grid min-h-screen place-items-center bg-[#08090b] px-5 text-zinc-100">
      <section className="w-full max-w-lg rounded-2xl border border-red-300/20 bg-red-300/[0.05] p-8">
        <p className="font-mono text-xs uppercase tracking-[0.2em] text-red-200">
          Detail unavailable
        </p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">
          This movie could not load right now.
        </h1>
        <p className="mt-3 text-sm leading-6 text-zinc-400">
          The catalog is still safe to browse. Retry this detail or return to the grid.
        </p>
        <div className="mt-6 flex gap-3">
          <button
            className="rounded-lg bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-950"
            onClick={reset}
            type="button"
          >
            Retry
          </button>
          <Link
            className="rounded-lg border border-white/15 px-4 py-2 text-sm text-zinc-200"
            href="/browse"
          >
            Browse movies
          </Link>
        </div>
      </section>
    </main>
  );
}
