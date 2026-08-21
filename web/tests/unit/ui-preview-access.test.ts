import { describe, expect, it } from "vitest";

import { isolatedUiPreviewMode } from "@/lib/ui-preview-access";

describe("isolatedUiPreviewMode", () => {
  it("requires the explicit fixture flag outside production", () => {
    expect(
      isolatedUiPreviewMode({ NODE_ENV: "test", MOVIELENS_UI_FIXTURE_MODE: "1" }),
    ).toBe(true);
    expect(
      isolatedUiPreviewMode({ NODE_ENV: "development", MOVIELENS_UI_FIXTURE_MODE: undefined }),
    ).toBe(false);
  });

  it("fails closed in production even when the fixture flag is set", () => {
    expect(
      isolatedUiPreviewMode({ NODE_ENV: "production", MOVIELENS_UI_FIXTURE_MODE: "1" }),
    ).toBe(false);
  });
});
