import { type Page } from "@playwright/test";

/**
 * Cold Start's shared restore, used by every journey that writes to it.
 *
 * The persona-ownership table in `browser-auth.spec.ts` gives 900000104 to two
 * journeys and requires them to hand it on with **zero positive signals** — it
 * is the only persona whose seeded state is "nothing", and every cold-start
 * assertion in the run, in the k6 page workload, and in the demo runbook reads
 * that emptiness back. Restoring it is subtle enough to be worth one place:
 *
 * - `DELETE …/rating` sets the star to null and *preserves the watched row*
 *   (ADR 0012: "delete rating: set rating to null; preserve watched state").
 * - `DELETE /users/{id}/ratings` is a loop over that same transition — the API
 *   calls it a "compatibility bulk rating clear; watched history is preserved"
 *   — so it is not the whole-persona reset its name suggests.
 * - `DELETE …/watched` is the only clear that takes the watched row with it,
 *   and with it the rating.
 *
 * The first two are what left one watched signal on Cold Start after a full
 * `test:e2e` run: the PKCE journey rated a title (a rating implies watched) and
 * then "reset" the persona with the bulk rating clear, which cleared the star
 * and kept the interaction. Nothing in the browser suites reads a count that
 * low, so it surfaced two suites later in k6's teardown guard.
 */

/** The persona the seeder leaves empty and every suite has to hand on empty. */
export const COLD_START = 900000104;

/**
 * The cold-start policy flips at five positive signals and the library caps a
 * page at fifty, so one page is far more than any leak these suites could
 * plausibly leave behind.
 */
const HISTORY_PAGE = 50;

export interface ColdStartContract {
  /** Whether serving reported the learned path for this persona. */
  learned: boolean;
  positiveSignals: number;
  policy: string;
  /** The watched titles the persona is carrying, newest first. */
  watched: number[];
  /** The library's own count, which is not capped by the page size above. */
  historyCount: number;
}

/**
 * Puts Cold Start back to zero positive signals, whatever it is carrying, and
 * returns the titles it had to remove.
 *
 * Deliberately indifferent to each response, and to its own failure: a persona
 * that is already clean is the state this is trying to reach, and a restore
 * that throws from inside a `finally` would replace the failure it was called
 * to preserve. Callers on the success path assert on the returned ids instead,
 * and the post-suite guard in `persona-hygiene.spec.ts` is the backstop.
 */
export async function resetColdStart(page: Page): Promise<number[]> {
  return page
    .evaluate(
      async ({ userId, limit }) => {
        const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
          .then((response) => response.json())
          .then((body: { csrfToken: string }) => body.csrfToken);
        const history = (await fetch(
          `/api/users/${userId}/library?tab=history&limit=${limit}`,
          { cache: "no-store" },
        ).then((response) => response.json())) as { items?: { movie_id: number }[] };
        const watched = (history.items ?? []).map((item) => item.movie_id);
        for (const movieId of watched) {
          await fetch(`/api/users/${userId}/movies/${movieId}/watched`, {
            method: "DELETE",
            headers: { "x-csrf-token": csrf, "Idempotency-Key": crypto.randomUUID() },
          });
        }
        return watched;
      },
      { userId: COLD_START, limit: HISTORY_PAGE },
    )
    .catch(() => []);
}

/**
 * Undoes a dismissal the way the deck's own undo control does.
 *
 * A dismissal is an exclusion rather than a positive signal, so it never moves
 * the count the guard reads — but it does change what serving returns for the
 * next run, so a journey that dismisses a title puts it back even when it
 * failed before its own undo step.
 */
export async function clearDismissal(page: Page, movieId: number): Promise<void> {
  await page
    .evaluate(
      async ({ userId, movie }) => {
        const csrf = await fetch("/api/auth/csrf", { cache: "no-store" })
          .then((response) => response.json())
          .then((body: { csrfToken: string }) => body.csrfToken);
        await fetch(`/api/users/${userId}/movies/${movie}/dismissal`, {
          method: "DELETE",
          headers: { "x-csrf-token": csrf, "Idempotency-Key": crypto.randomUUID() },
        });
      },
      { userId: COLD_START, movie: movieId },
    )
    .catch(() => {});
}

/**
 * Reads the two halves of Cold Start's contract in one round trip each: the
 * policy serving chose for it, and the history a reader would actually see.
 *
 * Both, because a leak can show up in either and they fail differently — the
 * policy is the model input, and the history names the title that got left
 * behind.
 */
export async function readColdStartContract(page: Page): Promise<ColdStartContract> {
  return page.evaluate(
    async ({ userId, limit }) => {
      const [served, history] = await Promise.all([
        fetch(`/api/users/${userId}/recommendations?limit=1`, { cache: "no-store" }).then(
          (response) =>
            response.json() as Promise<{
              serving_policy?: {
                name?: string;
                learned?: boolean;
                positive_signal_count?: number;
              };
            }>,
        ),
        fetch(`/api/users/${userId}/library?tab=history&limit=${limit}`, {
          cache: "no-store",
        }).then(
          (response) =>
            response.json() as Promise<{
              items?: { movie_id: number }[];
              counts?: { history?: number };
            }>,
        ),
      ]);
      const policy = served.serving_policy ?? {};
      return {
        learned: policy.learned ?? false,
        positiveSignals: policy.positive_signal_count ?? -1,
        policy: policy.name ?? "none",
        watched: (history.items ?? []).map((item) => item.movie_id),
        historyCount: history.counts?.history ?? -1,
      };
    },
    { userId: COLD_START, limit: HISTORY_PAGE },
  );
}
