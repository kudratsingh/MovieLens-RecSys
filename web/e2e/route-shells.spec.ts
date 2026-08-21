import { expect, test } from "@playwright/test";

const routes = [
  { path: "/ui-preview/discover", heading: "The Handmaiden" },
  { path: "/ui-preview/browse", heading: "Every good detour starts with a title." },
  { path: "/ui-preview/library", heading: "A record of what moved you." },
  { path: "/ui-preview/movies/101", heading: "The Handmaiden" },
  { path: "/ui-preview/quick-picks", heading: "Perfect Blue" },
] as const;

for (const route of routes) {
  test(`${route.path} renders a movie-first route shell without horizontal overflow`, async ({ page }) => {
    await page.goto(route.path);
    await expect(page.getByRole("heading", { level: 1, name: route.heading })).toBeVisible();
    await expect(page.getByRole("navigation", { name: /Primary/ })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
}

test("a recommendation evidence failure leaves recommendations usable", async ({ page }) => {
  await page.goto("/ui-preview/discover?fail=evidence");
  await expect(page.getByRole("heading", { level: 1, name: "The Handmaiden" })).toBeVisible();
  await expect(page.getByRole("alert", { name: "Technical evidence error" })).toContainText("Technical evidence is taking a night off");
  await expect(page.getByRole("link", { name: "Open movie" })).toBeVisible();
});

test("mobile uses the persistent bottom navigation", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-390", "Mobile shell assertion");
  await page.goto("/ui-preview/discover");
  await expect(page.getByRole("navigation", { name: "Primary mobile" })).toBeVisible();
  await page.getByRole("link", { name: "Browse", exact: true }).click();
  await expect(page).toHaveURL(/\/ui-preview\/browse$/);
});
