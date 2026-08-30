import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { ServingContractPanel } from "@/components/legacy/serving-contract-panel";
import type { ServingPolicy } from "@/lib/api";

const LEARNED: ServingPolicy = {
  excluded_count: 8,
  filter_policy: "watched-and-dismissed-excluded-v1",
  learned: true,
  name: "item-item-cosine+lightgbm",
  positive_signal_count: 12,
  reason: "learned-two-stage: item-item-cosine retrieval, ranked by demo-lgbm-v1",
  score_scale: "lightgbm-rank-score",
  threshold: 10,
};

/**
 * The panel that made the legacy dashboard a truthfulness failure.
 *
 * It asserted `Candidate policy: Popularity baseline` as a constant, in the
 * same session in which the API reported a learned two-stage policy. A model
 * claim that exceeds observed backend behaviour is a forbidden default, and a
 * legacy route is not an exemption from it.
 */
describe("the legacy serving-contract panel", () => {
  it("reports the policy the response carried", async () => {
    const { container } = render(
      <ServingContractPanel modelVersion="demo-lgbm-v1" policy={LEARNED} />,
    );

    expect(screen.getByTestId("serving-contract-policy")).toHaveTextContent(
      "item-item-cosine+lightgbm",
    );
    expect(screen.getByText("demo-lgbm-v1")).toBeVisible();
    expect(screen.getByText(LEARNED.reason)).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("never names a policy it was not told about", () => {
    render(<ServingContractPanel modelVersion={null} policy={null} />);

    expect(screen.getByTestId("serving-contract-policy")).toHaveTextContent(
      "Not read yet",
    );
    expect(screen.queryByText(/Popularity baseline/)).not.toBeInTheDocument();
  });

  it("says how far a cold-start persona is from learned serving", () => {
    render(
      <ServingContractPanel
        modelVersion="popularity-v1"
        policy={{ ...LEARNED, learned: false, name: "popularity", positive_signal_count: 2 }}
      />,
    );

    expect(screen.getByText("No — 2 of 10 positive signals")).toBeVisible();
  });
});
