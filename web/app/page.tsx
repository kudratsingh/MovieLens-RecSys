import { redirect } from "next/navigation";

import { auth, signIn } from "@/auth";
import { frontDoorHref, safeSignInReturn, signInDestination } from "@/lib/navigation";
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
 *
 * `?next=` is the address the viewer was trying to reach when a protected
 * route bounced them here. It is validated against the product's own routes on
 * the way in and again inside the sign-in action, and anything else is dropped
 * in favour of the default product address — an open redirect on the one
 * unauthenticated page in the app is not a trade worth making for a
 * convenience.
 */
export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{
    user?: string | string[];
    userId?: string | string[];
    next?: string | string[];
  }>;
}) {
  const [params, session] = await Promise.all([searchParams, auth()]);
  const returnTo = safeSignInReturn(params.next);

  if (!session?.user || session.error) {
    return (
      <SignInPage
        expired={session?.error === "RefreshAccessTokenError"}
        returnTo={returnTo}
      />
    );
  }

  redirect(frontDoorHref(params));
}

function SignInPage({
  expired,
  returnTo,
}: {
  expired: boolean;
  returnTo: string | null;
}) {
  const destination = returnTo ? signInDestination(returnTo) : null;

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
        {destination ? (
          // Styled with the door's existing vocabulary rather than a new rule:
          // the point is that the requested address survived, and saying so is
          // what turns a silent redirect into a promise the viewer can check.
          <p className="muted" role="status">
            You will land back on {destination} once you are signed in.
          </p>
        ) : null}
        <form
          action={async () => {
            "use server";
            // Re-validated rather than trusted. The closure value is encrypted
            // by the server-action bundle, but this is the app's only
            // unauthenticated surface and the check is one function call.
            await signIn("keycloak", { redirectTo: safeSignInReturn(returnTo) ?? "/" });
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
