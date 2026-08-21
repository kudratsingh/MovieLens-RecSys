"use client";

import { useState } from "react";

async function csrfToken() {
  const response = await fetch("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("Could not create a secure sign-out request");
  return ((await response.json()) as { csrfToken: string }).csrfToken;
}
export function SignOutButton() {
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    try {
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "x-csrf-token": await csrfToken() },
      });
      if (!response.ok) throw new Error("Sign out failed");
      window.location.assign("/");
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-zinc-300 transition hover:border-white/30 hover:text-white disabled:opacity-50"
      disabled={pending}
      onClick={() => void signOut()}
      type="button"
    >
      {pending ? "Signing out…" : "Sign out"}
    </button>
  );
}
