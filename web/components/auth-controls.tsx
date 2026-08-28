"use client";

import { useState } from "react";

import "./auth-controls.css";

async function csrfToken() {
  const response = await fetch("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not create a secure sign-out request");
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}

export function SignOutButton() {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function signOut() {
    setPending(true);
    setFailure(null);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "x-csrf-token": await csrfToken() },
      });
      if (!response.ok) throw new Error(`Sign out failed with ${response.status}`);
    } catch {
      // The button used to `try/finally` with no `catch`, and the call site
      // discarded the rejection with `void`, so a failed sign-out looked
      // exactly like one that had not been pressed — on a control whose whole
      // job is ending a session. Whatever went wrong, the session is still
      // live, and the viewer is the one who needs to know it.
      setFailure("Sign out did not complete. Your session is still open — try again.");
      setPending(false);
      return;
    }
    // `pending` is deliberately left set: the button stays disabled and
    // labelled until the navigation replaces this document.
    window.location.assign("/");
  }

  return (
    <span className="sign-out">
      <button
        className="button-quiet sign-out-button"
        disabled={pending}
        onClick={() => void signOut()}
        type="button"
      >
        {pending ? "Signing out…" : "Sign out"}
      </button>
      {failure ? (
        <span className="sign-out-failure" role="alert">
          {failure}
        </span>
      ) : null}
    </span>
  );
}
