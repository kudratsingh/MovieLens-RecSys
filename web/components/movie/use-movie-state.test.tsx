import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useMovieState } from "@/components/movie/use-movie-state";
import type { MovieState } from "@/lib/api";
import type { MovieStateClient } from "@/lib/movie-state/client";

const USER_ID = 900000101;

function movieState(overrides: Partial<MovieState> = {}): MovieState {
  return {
    tenant_id: "demo",
    user_id: USER_ID,
    movie_id: 101,
    rating: null,
    rating_updated_at: null,
    watched_at: null,
    watchlisted_at: null,
    dismissed_at: null,
    revision: 4,
    updated_at: "2026-08-20T12:00:00Z",
    ...overrides,
  };
}

/** Records what each write asserted, which is the whole point of the hook. */
function recordingClient(): MovieStateClient & { revisions: number[] } {
  const revisions: number[] = [];
  return {
    revisions,
    async mutate(input) {
      revisions.push(input.expectedRevision);
      return {
        status: "committed",
        state: movieState({
          movie_id: input.movieId,
          watchlisted_at: "2026-08-21T10:00:00Z",
          revision: input.expectedRevision + 1,
        }),
        outcome: "changed",
        replayed: false,
        requestId: "req-test",
      };
    },
    async readState() {
      return null;
    },
  };
}

/**
 * A deliberately un-keyed host: it re-points one hook instance at another
 * movie, which is exactly what every caller currently avoids by keying on the
 * movie id — and exactly what would silently send one movie's revision to
 * another if the hook did not reset.
 */
function Host({
  client,
  movieId,
  initialState,
}: {
  client: MovieStateClient;
  movieId: number;
  initialState: MovieState | null;
}) {
  const { display, message, state, run } = useMovieState({
    userId: USER_ID,
    movieId,
    title: `Movie ${movieId}`,
    initialState,
    client,
  });

  return (
    <div>
      <button onClick={() => void run({ resource: "watchlist", method: "PUT" })} type="button">
        Watchlist
      </button>
      <p data-testid="revision">{state?.revision ?? "none"}</p>
      <p data-testid="watchlisted">{String(display.watchlisted)}</p>
      <p data-testid="watched">{String(display.watched)}</p>
      <p data-testid="tone">{message?.tone ?? "none"}</p>
      <p data-testid="message">{message?.text ?? ""}</p>
    </div>
  );
}

describe("useMovieState", () => {
  it("rebinds to the movie it is pointed at instead of keeping the last one's revision", async () => {
    const client = recordingClient();
    const { rerender } = render(
      <Host client={client} initialState={movieState({ revision: 4 })} movieId={101} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await waitFor(() => expect(screen.getByTestId("revision")).toHaveTextContent("5"));

    rerender(
      <Host
        client={client}
        initialState={movieState({ movie_id: 202, revision: 9 })}
        movieId={202}
      />,
    );

    expect(screen.getByTestId("revision")).toHaveTextContent("9");
    expect(screen.getByTestId("watchlisted")).toHaveTextContent("false");

    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await waitFor(() => expect(client.revisions).toHaveLength(2));
    expect(client.revisions).toEqual([4, 9]);
  });

  it("adopts a strictly newer server render and ignores a stale one", async () => {
    const client = recordingClient();
    const { rerender } = render(
      <Host client={client} initialState={movieState({ revision: 4 })} movieId={101} />,
    );

    // Another surface committed and the route re-read: the next write has to
    // assert the revision the API issued, not the one this instance mounted on.
    rerender(<Host client={client} initialState={movieState({ revision: 7 })} movieId={101} />);
    expect(screen.getByTestId("revision")).toHaveTextContent("7");

    // A late or cached render carrying an older revision is not news.
    rerender(<Host client={client} initialState={movieState({ revision: 5 })} movieId={101} />);
    expect(screen.getByTestId("revision")).toHaveTextContent("7");

    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await waitFor(() => expect(client.revisions).toEqual([7]));
  });

  it("corrects the control from the record a refusal came back with", async () => {
    // A refusal is a rule about state, so being refused proves this control was
    // rendering something that is not stored. The write path re-reads and hands
    // the record over; leaving the control on the picture the API just
    // contradicted would keep offering an action that cannot succeed.
    const rule = "a watched movie cannot be added to the watchlist";
    const revisions: number[] = [];
    const client: MovieStateClient = {
      async mutate(input) {
        revisions.push(input.expectedRevision);
        return {
          status: "refused",
          requestId: "req-refused",
          detail: rule,
          canonical: movieState({ watched_at: "2026-08-21T10:00:00Z", revision: 9 }),
          corrected: true,
        };
      },
      async readState() {
        throw new Error("the write path owns the re-read; the hook must not repeat it");
      },
    };

    render(<Host client={client} initialState={movieState({ revision: 4 })} movieId={101} />);
    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));

    await waitFor(() => expect(screen.getByTestId("revision")).toHaveTextContent("9"));
    expect(screen.getByTestId("watched")).toHaveTextContent("true");
    expect(screen.getByTestId("watchlisted")).toHaveTextContent("false");
    // A note, not an error: nothing broke and nothing was stored.
    expect(screen.getByTestId("tone")).toHaveTextContent("note");
    expect(screen.getByTestId("message")).toHaveTextContent(rule);
    expect(screen.getByTestId("message")).toHaveTextContent("Its current state is shown.");

    // And the next press asserts the revision that came back, not the stale one.
    await userEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await waitFor(() => expect(revisions).toEqual([4, 9]));
  });
});
