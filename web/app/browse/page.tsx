import { Suspense } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { CatalogBrowser } from "@/components/catalog-browser";

export const metadata = {
  title: "Browse movies | MovieLens",
  description: "Search and filter the reviewed MovieLens demo catalog.",
};

export default async function BrowsePage({
  searchParams,
}: {
  searchParams: Promise<{ user?: string }>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  if (!session?.user || session.error) redirect("/");
  const userId = /^\d+$/.test(params.user ?? "") ? Number(params.user) : 900000101;
  return (
    <main className="min-h-screen bg-[#08090b] text-zinc-100">
      <div className="mx-auto max-w-[1500px] px-5 sm:px-8 lg:px-12">
        <CatalogHeader userId={userId} />
        <Suspense fallback={<div className="py-16 text-zinc-500">Preparing catalog…</div>}>
          <CatalogBrowser userId={userId} />
        </Suspense>
      </div>
    </main>
  );
}

function CatalogHeader({ userId }: { userId: number }) {
  return (
    <header className="flex items-center justify-between border-b border-white/10 py-5">
      <Link className="flex items-center gap-3" href="/">
        <span className="grid size-9 place-items-center rounded-xl bg-amber-300 font-black text-zinc-950">M</span>
        <span className="text-sm font-semibold">MovieLens</span>
      </Link>
      <nav className="flex items-center gap-1 text-sm" aria-label="Primary navigation">
        <Link className="rounded-lg px-3 py-2 text-zinc-400 hover:text-white" href="/">Discover</Link>
        <Link aria-current="page" className="rounded-lg bg-white/[0.07] px-3 py-2 text-white" href={`/browse?user=${userId}`}>Browse</Link>
        <Link className="rounded-lg px-3 py-2 text-zinc-400 hover:text-white" href={`/library?userId=${userId}`}>Library</Link>
      </nav>
      <span className="hidden rounded-full border border-white/10 px-3 py-1.5 text-xs text-zinc-400 sm:block">Demo persona · {userId}</span>
    </header>
  );
}
