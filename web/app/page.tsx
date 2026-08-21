import { redirect } from "next/navigation";

import { auth, signIn } from "@/auth";
import { frontDoorHref } from "@/lib/navigation";
import "./sign-in.css";

/**
 * The front door.
 *
 * Signed out, this is the sign-in entry and nothing else. Signed in, it hands
 * the viewer straight to the movie-discovery product: the first screen a
 * signed-in viewer meets has to be a movie and a movie decision, which is the
 * finish gate's first criterion and the one the pre-redesign dashboard failed
 * here.
 *
 * The dashboard is not gone — it lives at `/legacy`, which is the rollback the
 * implementation plan asks for. Pointing this route back at it is a one-line
 * change; `docs/frontend/README.md` records how.
 */
export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ user?: string | string[]; userId?: string | string[] }>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  if (!session?.user || session.error) {
    return <SignInPage expired={session?.error === "RefreshAccessTokenError"} />;
  }

  redirect(frontDoorHref(params));
}

function SignInPage({ expired }: { expired: boolean }) {
  return (
    <main className="sign-in-door">
      <section className="sign-in-card card-surface">
        <span aria-hidden="true" className="sign-in-mark">
          ML
        </span>
        <p className="eyebrow">MovieLens recommendation lab</p>
        <h1 className="section-title sign-in-title">
          Sign in to explore a real recommendation session.
        </h1>
        <p className="muted">
          Keycloak authenticates the browser with authorization code and PKCE. Tokens stay in
          an encrypted HttpOnly server session and never enter browser storage.
        </p>
        {expired ? (
          <p className="sign-in-expired" role="status">
            Your session expired and could not be refreshed. Sign in again to continue.
          </p>
        ) : null}
        <form
          action={async () => {
            "use server";
            await signIn("keycloak", { redirectTo: "/" });
          }}
          className="sign-in-form"
        >
          <button className="button-primary sign-in-submit" type="submit">
            Continue with Keycloak
          </button>
        </form>
        <p className="sign-in-note">
          Demo environment: use the seeded walkthrough account. The selected MovieLens persona
          is separate from the signed-in actor.
        </p>
      </section>
    </main>
  );
}
