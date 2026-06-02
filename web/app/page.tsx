export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center px-6 py-24 text-center">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
        MovieLens Recsys
      </h1>
      <p className="mt-4 max-w-xl text-base text-zinc-600 dark:text-zinc-400">
        Phase 3 scaffold. The recommendations UI, explainability panel, and
        champion-vs-challenger view land in subsequent PRs.
      </p>
      <p className="mt-8 text-xs uppercase tracking-widest text-zinc-500">
        Next.js {process.env.NODE_ENV === "production" ? "" : "(dev)"}
      </p>
    </main>
  );
}
