import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { ResourceRegion } from "@/components/ui/resource-region";
import {
  emptyState,
  failureState,
  loadingState,
  readyState,
  retryState,
  type ResourceFailureReason,
  type ResourceFailureStatus,
  type ResourceState,
} from "@/lib/resources/state";

const REQUEST_ID = "0f9d1c22-6a1a-4a26-9d1a-2b0f77e3f001";

type Payload = { title: string };

const ready = readyState<Payload>("recommendations", { title: "The Handmaiden" }, REQUEST_ID);

function failure(status: ResourceFailureStatus, reason: ResourceFailureReason) {
  return failureState({ status, resource: "recommendations", reason, requestId: REQUEST_ID });
}

function renderRegion(state: ResourceState<Payload>, onRetry?: () => void) {
  return render(
    <main>
      <ResourceRegion onRetry={onRetry} state={state}>
        {(data) => <p>{data.title}</p>}
      </ResourceRegion>
    </main>,
  );
}

describe("resource region states", () => {
  it("renders the region content when the resource is ready", async () => {
    const { container } = renderRegion(ready);

    expect(screen.getByText("The Handmaiden")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("announces loading and retrying without claiming a failure", async () => {
    const loading = renderRegion(loadingState("recommendations") as ResourceState<Payload>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading Recommendations");
    expect(await axe(loading.container)).toHaveNoViolations();
    loading.unmount();

    const retrying = renderRegion(
      retryState(failure("upstream-error", "timeout")) as ResourceState<Payload>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Retrying Recommendations");
    expect(await axe(retrying.container)).toHaveNoViolations();
  });

  it("treats an empty collection as a state of its own, not an error", async () => {
    const { container } = renderRegion(
      emptyState<Payload>("recommendations", { title: "" }, REQUEST_ID),
    );

    expect(screen.getByText("No recommendations yet")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("offers a reauthentication path when the session expired", async () => {
    const { container } = renderRegion(failure("auth-expired", "session-expired"));

    // The region is named by its headline, not by the transport status: the
    // enum is for logs, and a screen reader is not reading logs.
    const alert = screen.getByRole("alert", { name: "Your session expired" });
    expect(alert).toHaveTextContent("Your session expired");
    expect(screen.getByRole("link", { name: "Sign in again" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("names the forbidden and not-found outcomes distinctly", async () => {
    const forbidden = renderRegion(failure("forbidden", "forbidden"));
    expect(screen.getByRole("alert")).toHaveAccessibleName(
      "Recommendations this session cannot open",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Recommendations this session cannot open");
    expect(await axe(forbidden.container)).toHaveNoViolations();
    forbidden.unmount();

    const missing = renderRegion(failure("not-found", "not-found"));
    expect(screen.getByRole("alert")).toHaveAccessibleName("Recommendations not found");
    expect(screen.getByRole("alert")).toHaveTextContent("Recommendations not found");
    expect(await axe(missing.container)).toHaveNoViolations();
  });

  it("offers retry only where asking again could change the answer", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();

    const timeout = renderRegion(failure("upstream-error", "timeout"), onRetry);
    expect(screen.getByRole("alert")).toHaveTextContent("did not answer in time");
    expect(screen.getByText(`Request ${REQUEST_ID}`)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(await axe(timeout.container)).toHaveNoViolations();
    timeout.unmount();

    renderRegion(failure("upstream-error", "invalid-payload"), onRetry);
    expect(screen.getByRole("alert")).toHaveTextContent("did not match the published API contract");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});

describe("regions fail independently", () => {
  it("keeps catalog and Library readable when recommendations fail", async () => {
    const { container } = render(
      <main>
        <ResourceRegion state={failure("upstream-error", "server") as ResourceState<Payload>}>
          {(data) => <p>{data.title}</p>}
        </ResourceRegion>
        <ResourceRegion state={readyState("catalog", { title: "Browse the shelves" }, REQUEST_ID)}>
          {(data) => <p>{data.title}</p>}
        </ResourceRegion>
        <ResourceRegion state={readyState("library", { title: "Rated titles" }, REQUEST_ID)}>
          {(data) => <p>{data.title}</p>}
        </ResourceRegion>
      </main>,
    );

    expect(screen.getByText("Browse the shelves")).toBeVisible();
    expect(screen.getByText("Rated titles")).toBeVisible();
    expect(
      screen.getByRole("alert", { name: "Recommendations could not be loaded" }),
    ).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps the first movie visible when technical evidence fails", async () => {
    const { container } = render(
      <main>
        <h1>The Handmaiden</h1>
        <ResourceRegion
          state={
            failureState({
              status: "upstream-error",
              resource: "audits",
              reason: "timeout",
              requestId: REQUEST_ID,
            }) as ResourceState<Payload>
          }
        >
          {(data) => <p>{data.title}</p>}
        </ResourceRegion>
      </main>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "The Handmaiden" })).toBeVisible();
    expect(
      screen.getByRole("alert", { name: "Prediction audits could not be loaded" }),
    ).toBeVisible();
    expect(await axe(container)).toHaveNoViolations();
  });
});
