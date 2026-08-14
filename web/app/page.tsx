import { RecommendationDemo } from "@/components/recommendation-demo";

export default function Home() {
  return (
    <main className="min-h-screen bg-[#08090b] text-zinc-100">
      <div className="mx-auto max-w-[1500px] px-5 py-6 sm:px-8 lg:px-12">
        <header className="flex items-center justify-between border-b border-white/10 pb-5">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl bg-amber-300 font-black text-zinc-950">
              M
            </div>
            <div>
              <p className="text-sm font-semibold tracking-wide">MovieLens</p>
              <p className="text-xs text-zinc-500">Recommendation Lab</p>
            </div>
          </div>
          <div className="hidden items-center gap-2 rounded-full border border-emerald-400/20 bg-emerald-400/5 px-3 py-1.5 text-xs text-emerald-300 sm:flex">
            <span className="size-1.5 rounded-full bg-emerald-300" />
            Phase 3 · online baseline
          </div>
        </header>

        <section className="grid gap-8 pb-8 pt-14 lg:grid-cols-[minmax(0,1fr)_380px] lg:gap-12">
          <div>
            <p className="mb-4 font-mono text-xs uppercase tracking-[0.28em] text-amber-300">
              Two-stage recommender
            </p>
            <h1 className="max-w-4xl text-4xl font-semibold leading-[1.05] tracking-[-0.045em] sm:text-6xl lg:text-7xl">
              Movies selected with the system visible.
            </h1>
            <p className="mt-6 max-w-2xl text-base leading-7 text-zinc-400 sm:text-lg">
              Explore tenant-scoped recommendations, inspect recent history,
              and see which serving policy produced every result.
            </p>
          </div>

          <aside className="rounded-2xl border border-white/10 bg-white/[0.035] p-5">
            <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">
              Serving contract
            </p>
            <dl className="mt-5 space-y-4 text-sm">
              <div className="flex justify-between gap-6">
                <dt className="text-zinc-500">Candidate policy</dt>
                <dd>Popularity baseline</dd>
              </div>
              <div className="flex justify-between gap-6">
                <dt className="text-zinc-500">Isolation</dt>
                <dd>Postgres RLS</dd>
              </div>
              <div className="flex justify-between gap-6">
                <dt className="text-zinc-500">Target latency</dt>
                <dd className="font-mono text-amber-300">p99 &lt; 100 ms</dd>
              </div>
            </dl>
          </aside>
        </section>

        <RecommendationDemo />
      </div>
    </main>
  );
}
