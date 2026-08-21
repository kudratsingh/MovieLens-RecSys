import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    exclude: ["e2e/**", "tests/e2e/**", "node_modules/**"],
    globals: true,
    include: ["tests/unit/**/*.test.ts", "components/**/*.test.tsx"],
    restoreMocks: true,
    setupFiles: ["./test/setup.ts"],
  },
});
