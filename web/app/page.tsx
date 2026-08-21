import { auth, signIn } from "@/auth";
import { SignOutButton } from "@/components/auth-controls";
import { RecommendationDemo } from "@/components/recommendation-demo";
import Link from "next/link";

export default async function Home() {
  const session = await auth();
  if (!session?.user || session.error) {
    return <SignInPage expired={session?.error === "RefreshAccessTokenError"} />;
  }

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
          <div className="flex items-center gap-3">
            <nav className="hidden items-center gap-1 text-sm md:flex" aria-label="Primary navigation">
              <Link aria-current="page" className="rounded-lg bg-white/[0.07] px-3 py-2" href="/">Discover</Link>
              <Link className="rounded-lg px-3 py-2 text-zinc-400 transition hover:text-white" href="/browse?user=900000101">Browse</Link>
              <Link className="rounded-lg px-3 py-2 text-zinc-400 transition hover:text-white" href="/library?userId=900000101">Library</Link>
            </nav>
            <div className="hidden text-right sm:block">
              <p className="text-xs font-medium text-zinc-200">
                {session.user.name ?? session.user.email ?? "Signed-in actor"}
              </p>
              <p className="text-[11px] text-zinc-500">Demo persona access</p>
            </div>
            <SignOutButton />
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

function SignInPage({ expired }: { expired: boolean }) {
  return (
    <main className="grid min-h-screen place-items-center bg-[#08090b] px-5 text-zinc-100">
      <section className="w-full max-w-lg rounded-3xl border border-white/10 bg-white/[0.035] p-8 shadow-2xl shadow-black/30 sm:p-10">
        <div className="grid size-12 place-items-center rounded-2xl bg-amber-300 text-lg font-black text-zinc-950">
          M
        </div>
        <p className="mt-8 font-mono text-xs uppercase tracking-[0.25em] text-amber-300">
          MovieLens recommendation lab
        </p>
        <h1 className="mt-4 text-4xl font-semibold tracking-[-0.04em]">
          Sign in to explore a real recommendation session.
        </h1>
        <p className="mt-4 text-sm leading-6 text-zinc-400">
          Keycloak authenticates the browser with authorization code and PKCE. Tokens stay in
          an encrypted HttpOnly server session and never enter browser storage.
        </p>
        {expired ? (
          <p className="mt-5 rounded-xl border border-amber-300/20 bg-amber-300/5 px-4 py-3 text-sm text-amber-100">
            Your session expired and could not be refreshed. Sign in again to continue.
          </p>
        ) : null}
        <form
          action={async () => {
            "use server";
            await signIn("keycloak", { redirectTo: "/" });
          }}
          className="mt-8"
        >
          <button
            className="w-full rounded-xl bg-amber-300 px-5 py-3 text-sm font-bold text-zinc-950 transition hover:bg-amber-200 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-amber-300"
            type="submit"
          >
            Continue with Keycloak
          </button>
        </form>
        <p className="mt-4 text-xs leading-5 text-zinc-600">
          Demo environment: use the seeded walkthrough account. The selected MovieLens persona
          is separate from the signed-in actor.
        </p>
      </section>
    </main>
  );
}
