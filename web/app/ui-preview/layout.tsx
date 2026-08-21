import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { AppShell } from "@/components/shell/app-shell";
import { FrontendErrorBoundary } from "@/components/ui/error-boundary";
import { isolatedUiPreviewMode } from "@/lib/ui-preview-access";
import "@/components/shell/shell.css";

export default async function ProductLayout({ children }: { children: React.ReactNode }) {
  const fixtureMode = isolatedUiPreviewMode();
  const session = fixtureMode ? null : await auth();
  if (!fixtureMode && (!session?.user || session.error)) redirect("/");

  return (
    <AppShell
      actorName={
        fixtureMode
          ? "Fixture reviewer"
          : (session?.user?.name ?? session?.user?.email ?? "Signed-in actor")
      }
      fixtureMode={fixtureMode}
    >
      <FrontendErrorBoundary label="This page">{children}</FrontendErrorBoundary>
    </AppShell>
  );
}
