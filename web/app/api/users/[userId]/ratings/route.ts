const API_BASE_URL = process.env.RECOMMENDATION_API_URL ?? "http://localhost:8000";

function requestHeaders(request: Request) {
  const authorization = request.headers.get("authorization");
  return {
    "Content-Type": "application/json",
    ...(authorization ? { Authorization: authorization } : {}),
  };
}

export async function POST(
  request: Request,
  context: { params: Promise<{ userId: string }> },
) {
  const { userId } = await context.params;
  const body = (await request.json().catch(() => null)) as
    | { movie_id?: unknown; rating?: unknown }
    | null;
  if (!/^\d+$/.test(userId) || !body || typeof body.movie_id !== "number") {
    return Response.json({ detail: "Invalid rating request" }, { status: 400 });
  }
  try {
    const response = await fetch(
      `${API_BASE_URL}/users/${userId}/ratings/${body.movie_id}`,
      {
        method: "PUT",
        headers: requestHeaders(request),
        body: JSON.stringify({ rating: body.rating }),
      },
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Could not reach the recommendation API" }, { status: 502 });
  }
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ userId: string }> },
) {
  const { userId } = await context.params;
  if (!/^\d+$/.test(userId)) {
    return Response.json({ detail: "Invalid MovieLens user ID" }, { status: 400 });
  }
  try {
    const response = await fetch(`${API_BASE_URL}/users/${userId}/ratings`, {
      method: "DELETE",
      headers: requestHeaders(request),
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Could not reach the recommendation API" }, { status: 502 });
  }
}
