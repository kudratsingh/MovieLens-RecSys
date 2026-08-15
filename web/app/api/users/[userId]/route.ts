import type { UserDashboard } from "@/lib/api";

const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

export async function GET(
  request: Request,
  context: { params: Promise<{ userId: string }> },
) {
  const { userId } = await context.params;
  if (!/^\d+$/.test(userId) || Number(userId) < 1) {
    return Response.json({ detail: "Invalid MovieLens user ID" }, { status: 400 });
  }

  const authorization = request.headers.get("authorization");
  const headers = authorization ? { Authorization: authorization } : undefined;

  try {
    const [recommendationsResponse, historyResponse, catalogResponse] = await Promise.all([
      fetch(`${API_BASE_URL}/users/${userId}/recommendations?limit=8`, {
        cache: "no-store",
        headers,
      }),
      fetch(`${API_BASE_URL}/users/${userId}/history?limit=8`, {
        cache: "no-store",
        headers,
      }),
      fetch(`${API_BASE_URL}/users/${userId}/catalog`, {
        cache: "no-store",
        headers,
      }),
    ]);

    if (!recommendationsResponse.ok || !historyResponse.ok || !catalogResponse.ok) {
      const failed = !recommendationsResponse.ok
        ? recommendationsResponse
        : !historyResponse.ok
          ? historyResponse
          : catalogResponse;
      const body = (await failed.json().catch(() => null)) as { detail?: string } | null;
      return Response.json(
        { detail: body?.detail ?? `Recommendation API returned ${failed.status}` },
        { status: failed.status },
      );
    }

    const dashboard: UserDashboard = {
      recommendations: await recommendationsResponse.json(),
      history: await historyResponse.json(),
      catalog: await catalogResponse.json(),
    };
    return Response.json(dashboard);
  } catch {
    return Response.json(
      {
        detail: `Could not reach the recommendation API at ${API_BASE_URL}. Start it with make serve.`,
      },
      { status: 502 },
    );
  }
}
