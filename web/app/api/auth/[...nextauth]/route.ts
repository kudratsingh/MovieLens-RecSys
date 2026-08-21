import { handlers } from "@/auth";
import type { NextRequest } from "next/server";

const SENSITIVE_SESSION_FIELDS = [
  "accessToken",
  "refreshToken",
  "idToken",
] as const;

export async function GET(request: NextRequest) {
  const response = await handlers.GET(request);
  if (!new URL(request.url).pathname.endsWith("/session") || !response.ok) {
    return response;
  }

  const session = (await response.json()) as Record<string, unknown> | null;
  if (session) {
    for (const field of SENSITIVE_SESSION_FIELDS) delete session[field];
  }
  const headers = new Headers(response.headers);
  headers.delete("content-length");
  headers.set("content-type", "application/json");
  return new Response(JSON.stringify(session), { status: response.status, headers });
}

export const POST = handlers.POST;
