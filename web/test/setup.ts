import "@testing-library/jest-dom/vitest";

import { toHaveNoViolations } from "jest-axe";
import { createElement } from "react";
import { expect, vi } from "vitest";

expect.extend(toHaveNoViolations);

vi.mock("next/navigation", () => ({
  usePathname: () => "/discover",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
    push: vi.fn(),
    refresh: vi.fn(),
    replace: vi.fn(),
  }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: React.ReactNode; href: string }) =>
    createElement("a", { href, ...props }, children),
}));

// jsdom implements neither, and the movie surfaces call both: a rail scrolls
// itself by a card, and Discover hands the viewer back to the featured movie
// after a rating. Tests that care about the second one assert against this mock.
Object.defineProperty(Element.prototype, "scrollBy", {
  configurable: true,
  value: vi.fn(),
});

Object.defineProperty(Element.prototype, "scrollIntoView", {
  configurable: true,
  value: vi.fn(),
});
