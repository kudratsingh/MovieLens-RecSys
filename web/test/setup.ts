import "@testing-library/jest-dom/vitest";

import { toHaveNoViolations } from "jest-axe";
import { createElement } from "react";
import { expect, vi } from "vitest";

expect.extend(toHaveNoViolations);

vi.mock("next/navigation", () => ({
  usePathname: () => "/discover",
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: React.ReactNode; href: string }) =>
    createElement("a", { href, ...props }, children),
}));

Object.defineProperty(Element.prototype, "scrollBy", {
  configurable: true,
  value: vi.fn(),
});
