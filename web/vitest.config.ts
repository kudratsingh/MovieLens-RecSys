import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
      // Next resolves this specifier itself; Vitest needs a stand-in so
      // server-guarded modules stay testable. See test/server-only-stub.ts.
      "server-only": fileURLToPath(
        new URL("./test/server-only-stub.ts", import.meta.url),
      ),
    },
  },
  test: {
    environment: "jsdom",
    exclude: ["e2e/**", "tests/e2e/**", "node_modules/**"],
    globals: true,
    include: ["tests/unit/**/*.test.ts", "components/**/*.test.tsx"],
    restoreMocks: true,
    setupFiles: ["./test/setup.ts"],
    // Vitest's 5 s default is a per-test budget measured on an idle machine.
    // `library-experience.test.tsx` renders three tab views and drives an
    // optimistic write through a fake client; it settles in ~1.5 s on its own
    // and times out under full-suite load, which reads as a product failure in
    // CI when it is only contention. This is a ceiling for a hung test, not a
    // waiting budget any test is expected to approach.
    testTimeout: 15_000,
  },
});
