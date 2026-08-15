import type { PersonaResponse } from "@/lib/api";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

export async function GET(request: Request) {
  const authorization = request.headers.get("authorization");
  const headers = authorization ? { Authorization: authorization } : undefined;

  try {
    const response = await fetch(`${API_BASE_URL}/personas`, {
      cache: "no-store",
      headers,
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      return Response.json(
        { detail: body?.detail ?? `Recommendation API returned ${response.status}` },
        { status: response.status },
      );
    }
    return Response.json((await response.json()) as PersonaResponse);
  } catch {
    return Response.json(
      {
        detail: `Could not reach the recommendation API at ${API_BASE_URL}. Start it with make serve.`,
      },
      { status: 502 },
    );
  }
}
