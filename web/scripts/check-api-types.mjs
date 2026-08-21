import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temporaryDirectory = mkdtempSync(join(tmpdir(), "movielens-openapi-"));
const generatedPath = join(temporaryDirectory, "api.generated.ts");

try {
  execFileSync(
    join(webRoot, "node_modules", ".bin", "openapi-typescript"),
    [resolve(webRoot, "..", "docs", "api", "openapi.json"), "-o", generatedPath],
    { cwd: webRoot, stdio: "inherit" },
  );
  const committed = readFileSync(join(webRoot, "lib", "api.generated.ts"), "utf8");
  const generated = readFileSync(generatedPath, "utf8");
  if (committed !== generated) {
    throw new Error("web/lib/api.generated.ts is stale; run npm run api:types");
  }
} finally {
  rmSync(temporaryDirectory, { recursive: true, force: true });
}
