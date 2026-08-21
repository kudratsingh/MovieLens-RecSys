import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { LibraryExperience } from "@/components/library-experience";

type LibrarySearch = {
  userId?: string | string[];
  tab?: string | string[];
  sort?: string | string[];
  q?: string | string[];
};

function single(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LibraryPage({
  searchParams,
}: {
  searchParams: Promise<LibrarySearch>;
}) {
  const session = await auth();
  if (!session?.user || session.error) redirect("/");

  const query = await searchParams;
  const requestedTab = single(query.tab);
  const requestedSort = single(query.sort);
  return (
    <LibraryExperience
      actorName={session.user.name ?? session.user.email ?? "Signed-in actor"}
      initialQuery={single(query.q) ?? ""}
      initialSort={
        requestedSort === "title" || requestedSort === "rating" ? requestedSort : "recent"
      }
      initialTab={
        requestedTab === "watchlist" || requestedTab === "history" ? requestedTab : "rated"
      }
      key={`${single(query.userId) ?? "900000101"}:${requestedTab ?? "rated"}:${requestedSort ?? "recent"}:${single(query.q) ?? ""}`}
      userId={Number(single(query.userId) ?? "900000101") || 900000101}
    />
  );
}
