import { mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const here = dirname(fileURLToPath(import.meta.url));
const output = resolve(here, "../../docs/frontend/evidence/bundle-4");
const baseURL = process.env.EVIDENCE_BASE_URL ?? "http://localhost:3104";
const captures = [
  { name: "discover-mobile", path: "/ui-preview/discover", width: 390, height: 844 },
  { name: "discover-tablet", path: "/ui-preview/discover", width: 768, height: 1024 },
  { name: "discover-desktop", path: "/ui-preview/discover", width: 1440, height: 1000 },
  { name: "browse-mobile", path: "/ui-preview/browse", width: 390, height: 844 },
  { name: "browse-desktop", path: "/ui-preview/browse", width: 1440, height: 1000 },
  { name: "library-mobile", path: "/ui-preview/library", width: 390, height: 844 },
  { name: "library-desktop", path: "/ui-preview/library", width: 1440, height: 1000 },
  { name: "partial-evidence-error", path: "/ui-preview/discover?fail=evidence", width: 1440, height: 1000 },
];

await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: "dark" });

for (const capture of captures) {
  await page.setViewportSize({ width: capture.width, height: capture.height });
  await page.goto(`${baseURL}${capture.path}`, { waitUntil: "networkidle" });
  await page.screenshot({
    animations: "disabled",
    path: resolve(output, `${capture.name}.png`),
  });
}

await browser.close();
