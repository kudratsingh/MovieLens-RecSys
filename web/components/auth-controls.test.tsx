import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SignOutButton } from "@/components/auth-controls";

/**
 * Sign out is the shell's only account control, and it used to fail silently:
 * `try/finally` with no `catch`, and a call site that discarded the rejection
 * with `void`. A network failure, a rejected CSRF token, and a 500 all looked
 * exactly like a button that had not been pressed — on the one control whose
 * failure leaves a live session behind.
 */

const originalLocation = window.location;

function stubNavigation() {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, assign },
  });
  return assign;
}

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

function respondTo(handlers: Record<string, () => Response | Promise<Response>>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const handler = Object.entries(handlers).find(([path]) => url.includes(path))?.[1];
    if (!handler) throw new Error(`unexpected request to ${url}`);
    return handler();
  });
}

const csrfOk = () => Response.json({ csrfToken: "token" });

describe("the sign-out control", () => {
  it("ends the session and replaces the document", async () => {
    const assign = stubNavigation();
    vi.stubGlobal(
      "fetch",
      respondTo({
        "/api/auth/csrf": csrfOk,
        "/api/auth/logout": () => new Response(null, { status: 204 }),
      }),
    );

    render(<SignOutButton />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says so when the sign-out request is refused", async () => {
    const assign = stubNavigation();
    vi.stubGlobal(
      "fetch",
      respondTo({
        "/api/auth/csrf": csrfOk,
        "/api/auth/logout": () => new Response(null, { status: 403 }),
      }),
    );

    render(<SignOutButton />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your session is still open");
    // Nothing navigated, so the button has to come back rather than stay in a
    // permanent "Signing out…" that cannot be retried.
    expect(assign).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });

  it("says so when the CSRF token cannot be minted", async () => {
    // The failure that reaches this component as a thrown error rather than as
    // a non-ok logout response.
    const assign = stubNavigation();
    vi.stubGlobal(
      "fetch",
      respondTo({ "/api/auth/csrf": () => new Response(null, { status: 500 }) }),
    );

    render(<SignOutButton />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(assign).not.toHaveBeenCalled();
  });

  it("clears a previous failure when the viewer tries again", async () => {
    const assign = stubNavigation();
    let attempt = 0;
    vi.stubGlobal(
      "fetch",
      respondTo({
        "/api/auth/csrf": csrfOk,
        "/api/auth/logout": () => {
          attempt += 1;
          return new Response(null, { status: attempt === 1 ? 500 : 204 });
        },
      }),
    );

    render(<SignOutButton />);
    const button = screen.getByRole("button", { name: "Sign out" });
    await userEvent.click(button);
    expect(await screen.findByRole("alert")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("has no accessibility violations in either state", async () => {
    stubNavigation();
    vi.stubGlobal(
      "fetch",
      respondTo({
        "/api/auth/csrf": csrfOk,
        "/api/auth/logout": () => new Response(null, { status: 500 }),
      }),
    );

    const { container } = render(<SignOutButton />);
    expect(await axe(container)).toHaveNoViolations();

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("alert");
    expect(await axe(container)).toHaveNoViolations();
  });
});
