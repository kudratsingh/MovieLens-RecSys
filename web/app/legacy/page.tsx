import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { SignOutButton } from "@/components/auth-controls";
import { RecommendationDemo } from "@/components/legacy/recommendation-demo";
import { frontDoorHref } from "@/lib/navigation";

export const metadata: Metadata = {
  title: "Legacy dashboard",
  description:
    "The pre-redesign Phase 3 dashboard, retained as the rollback for the movie-discovery cutover.",
  robots: { index: false },
};

/**
 * The pre-redesign dashboard, in its permanent home.
 *
 * Until the cutover this component *was* `/`, and `/legacy` re-exported it —
 * so the dashboard was the default route and the legacy route was its alias,
 * which is the opposite of what the implementation plan describes. It is now
 * only here, it says so on screen, and it is not in the primary navigation of
 * any product route.
 *
 * It stays until a participant-backed PASS retires it in its own PR. Removing
 * it before then would leave the cutover with no rollback.
 */
export default async function LegacyDashboard() {
  const session = await auth();
  // Same door as every other authenticated route, rather than a second
  // sign-in surface that would drift from the real one.
  if (!session?.user || session.error) redirect("/");

  const productHref = frontDoorHref({});

  return (
    <main className="min-h-screen bg-[#08090b] text-zinc-100">
      <div className="mx-auto max-w-[1500px] px-5 py-6 sm:px-8 lg:px-12">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-white/10 pb-5">
          <div className="flex items-center gap-3">
            <div className="grid size-10 place-items-center rounded-xl bg-amber-300 font-black text-zinc-950">
              M
            </div>
            <div>
              <p className="text-sm font-semibold tracking-wide">MovieLens</p>
              <p className="text-xs text-zinc-500">Legacy dashboard</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-xs font-medium text-zinc-200">
                {session.user.name ?? session.user.email ?? "Signed-in actor"}
              </p>
              <p className="text-[11px] text-zinc-500">Demo persona access</p>
            </div>
            <SignOutButton />
          </div>
        </header>

        <p
          className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-amber-300/20 bg-amber-300/5 px-4 py-3 text-sm text-amber-100"
          role="status"
        >
          <span>
            This is the legacy dashboard — the pre-redesign Phase 3 surface, kept as
            the rollback for the movie-discovery cutover.
          </span>
          <Link
            className="rounded-lg border border-amber-300/40 px-3 py-1.5 text-xs font-semibold text-amber-100 transition hover:border-amber-300 hover:text-white"
            href={productHref}
          >
            Open the movie-discovery product
          </Link>
        </p>

        <RecommendationDemo
          intro={
            <div>
              <p className="mb-4 font-mono text-xs uppercase tracking-[0.28em] text-amber-300">
                Two-stage recommender
              </p>
              <h1 className="max-w-4xl text-4xl font-semibold leading-[1.05] tracking-[-0.045em] sm:text-6xl lg:text-7xl">
                Movies selected with the system visible.
              </h1>
              <p className="mt-6 max-w-2xl text-base leading-7 text-zinc-400">
                Explore tenant-scoped recommendations, inspect recent history,
                and see which serving policy produced every result.
              </p>
            </div>
          }
        />
      </div>
    </main>
  );
}
