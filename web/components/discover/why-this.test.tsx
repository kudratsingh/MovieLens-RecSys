import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WhyThis } from "@/components/discover/why-this";
import type { RecommendationResponse } from "@/lib/api";
import {
  discoverAudits,
  discoverFeatures,
  fallbackAudits,
  fallbackRecommendations,
  learnedRecommendations,
} from "@/lib/fixtures/discover-fixtures";
import { failureState, readyState } from "@/lib/resources/state";

const REQUEST_ID = "6f1b4c02-9f3d-4a01-8a6c-31c0f9a2b7d4";

const recorded = {
  audits: readyState("audits", discoverAudits, REQUEST_ID),
  features: readyState("features", discoverFeatures, REQUEST_ID),
};

function renderWhyThis(
  response: RecommendationResponse,
  preloadedEvidence: typeof recorded | null = recorded,
) {
  return render(
    <main>
      <WhyThis
        item={response.items[0]}
        preloadedEvidence={preloadedEvidence}
        requestId={REQUEST_ID}
        response={response}
        userId={900000101}
      />
    </main>,
  );
}

function drawer() {
  return within(screen.getByRole("dialog"));
}

afterEach(() => vi.unstubAllGlobals());

describe("Why this? shows only evidence that exists", () => {
  it("repeats the API's own reason and the reported policy", async () => {
    const user = userEvent.setup();
    const { container } = renderWhyThis(learnedRecommendations);

    await user.click(screen.getByRole("button", { name: "Why this?" }));

    expect(
      drawer().getByText("LightGBM rank over learned item-item candidates"),
    ).toBeVisible();
    expect(drawer().getByText(/Ranked by the learned model/)).toBeVisible();
    expect(drawer().getByText("item-item-lightgbm")).toBeVisible();
    expect(drawer().getByText(REQUEST_ID)).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("never fabricates a liked-title claim or a match percentage", async () => {
    const user = userEvent.setup();
    const { container } = renderWhyThis(learnedRecommendations);
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));

    const text = (container.textContent ?? "").toLowerCase();
    expect(text).not.toContain("because you liked");
    expect(text).not.toContain("% match");
    expect(text).not.toContain("match score");
    expect(text).toContain("not a probability or a match percentage");
    // The raw ranker value is shown, but only as an ordering value.
    expect(drawer().getByText("4.8213")).toBeVisible();
  });

  it("points a fallback response at its recorded reason instead of guessing", async () => {
    const user = userEvent.setup();
    renderWhyThis(fallbackRecommendations, {
      audits: readyState("audits", fallbackAudits, REQUEST_ID),
      features: recorded.features,
    });

    await user.click(screen.getByRole("button", { name: "Why this?" }));
    expect(drawer().getByText(/Popular while we learn/)).toBeVisible();
    // The response reports its own counts, so the note quotes those rather
    // than restating the generic routing rule.
    expect(drawer().getByText(/3 of the 5 watched signals/)).toBeVisible();
    expect(
      drawer().getByText("cold-start: 3 positive watched signals below threshold 5"),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));
    expect(drawer().getByText("cold-start")).toBeVisible();
  });

  it("keeps the deep technical evidence to two deliberate actions", async () => {
    const user = userEvent.setup();
    renderWhyThis(learnedRecommendations);

    expect(screen.queryByTestId("technical-evidence")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    expect(screen.queryByTestId("technical-evidence")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));
    expect(screen.getByTestId("technical-evidence")).toBeVisible();
  });

  it("keeps the reason readable when the audit resource fails", async () => {
    const user = userEvent.setup();
    renderWhyThis(learnedRecommendations, {
      audits: failureState({
        status: "upstream-error",
        resource: "audits",
        reason: "server",
        requestId: REQUEST_ID,
      }),
      features: failureState({
        status: "forbidden",
        resource: "features",
        reason: "forbidden",
        requestId: REQUEST_ID,
      }),
    });

    await user.click(screen.getByRole("button", { name: "Why this?" }));
    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));

    expect(
      drawer().getByText("LightGBM rank over learned item-item candidates"),
    ).toBeVisible();
    expect(drawer().getByRole("alert", { name: /Prediction audits/ })).toBeVisible();
    expect(drawer().getByRole("alert", { name: /Online features/ })).toBeVisible();
  });

  it("fetches evidence from the BFF only when a reader asks for it", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) =>
      String(input).includes("/audits")
        ? Response.json(discoverAudits)
        : Response.json(discoverFeatures),
    );
    vi.stubGlobal("fetch", fetchImpl);

    renderWhyThis(learnedRecommendations, null);
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    expect(fetchImpl).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/users/900000101/audits?limit=5",
      "/api/users/900000101/features",
    ]);
    expect(await drawer().findByText("feast-online-redis")).toBeVisible();
  });
});

describe("Why this? answers in a sentence before it answers in a table", () => {
  it("opens with plain language and keeps every identifier below it", async () => {
    const user = userEvent.setup();
    renderWhyThis(learnedRecommendations);

    await user.click(screen.getByRole("button", { name: "Why this?" }));

    const opening = drawer().getByText(/^Picked from/);
    expect(opening).toBeVisible();
    expect(opening.textContent).not.toMatch(/lightgbm|item-item|sha256/i);
    // The evidence is unchanged and one heading further down, not one action.
    expect(drawer().getByRole("heading", { name: "Model evidence" })).toBeVisible();
    expect(drawer().getByText("item-item-lightgbm")).toBeVisible();
  });

  it("gives a fallback response its own sentence rather than a borrowed one", async () => {
    const user = userEvent.setup();
    renderWhyThis(fallbackRecommendations);

    await user.click(screen.getByRole("button", { name: "Why this?" }));

    expect(drawer().getByText(/watched most across this tenant/)).toBeVisible();
    expect(drawer().queryByText(/^Picked from/)).not.toBeInTheDocument();
  });

  it("never tells a warm persona it has N of the 5 signals", async () => {
    const user = userEvent.setup();
    const warm: RecommendationResponse = {
      ...fallbackRecommendations,
      serving_policy: {
        ...fallbackRecommendations.serving_policy,
        positive_signal_count: 28,
      },
    };
    renderWhyThis(warm, null);

    await user.click(screen.getByRole("button", { name: "Why this?" }));

    // The opening paragraph, which is where a viewer reads it first; the same
    // sentence also stands as the policy note below.
    const opening = drawer().getByText(/^These are the titles watched most/);
    expect(opening).toHaveTextContent("enough watched signals");
    expect(drawer().queryByText(/28 of the 5/)).not.toBeInTheDocument();
    // …and it is not labelled as cold start either.
    expect(drawer().queryByText(/Popular while we learn/)).not.toBeInTheDocument();
  });

  it("keeps the technical evidence exactly two deliberate actions away", async () => {
    const user = userEvent.setup();
    renderWhyThis(learnedRecommendations);

    await user.click(screen.getByRole("button", { name: "Why this?" }));
    expect(screen.queryByTestId("technical-evidence")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));
    expect(screen.getByTestId("technical-evidence")).toBeVisible();
  });

  it("retries the online-features read once before reporting it failed", async () => {
    const user = userEvent.setup();
    const featureCalls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/audits")) return Response.json(discoverAudits);
      featureCalls.push(url);
      // The dependency answered once during review with a 503 on a request
      // that succeeded immediately afterwards; nobody is waiting on it.
      return featureCalls.length === 1
        ? Response.json({ detail: "feature server unavailable" }, { status: 503 })
        : Response.json(discoverFeatures);
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderWhyThis(learnedRecommendations, null);
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));

    expect(await drawer().findByText("feast-online-redis")).toBeVisible();
    expect(featureCalls).toHaveLength(2);
    expect(drawer().queryByRole("alert", { name: /Online features/ })).not.toBeInTheDocument();
  });

  it("reports a failure that a second attempt could not fix", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async (input: string | URL | Request) =>
      String(input).includes("/audits")
        ? Response.json(discoverAudits)
        : Response.json({ detail: "feature server unavailable" }, { status: 503 }),
    );
    vi.stubGlobal("fetch", fetchImpl);

    renderWhyThis(learnedRecommendations, null);
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));

    expect(
      await drawer().findByRole("alert", { name: /Online features/ }),
    ).toBeVisible();
  });

  it("does not retry a failure asking again cannot change", async () => {
    const user = userEvent.setup();
    const featureCalls: string[] = [];
    const fetchImpl = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/audits")) return Response.json(discoverAudits);
      featureCalls.push(url);
      return Response.json({ detail: "not allowed" }, { status: 403 });
    });
    vi.stubGlobal("fetch", fetchImpl);

    renderWhyThis(learnedRecommendations, null);
    await user.click(screen.getByRole("button", { name: "Why this?" }));
    await user.click(screen.getByRole("button", { name: "Show prediction audit" }));

    expect(await drawer().findByRole("alert", { name: /Online features/ })).toBeVisible();
    expect(featureCalls).toHaveLength(1);
  });
});
