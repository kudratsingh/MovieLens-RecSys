"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Icon } from "@/components/ui/icons";

const items = [
  { href: "/ui-preview/discover", label: "For you", icon: "spark" as const },
  { href: "/ui-preview/browse", label: "Browse", icon: "compass" as const },
  { href: "/ui-preview/library", label: "Library", icon: "library" as const },
];

export function ProductNavigation({
  location,
}: {
  location: "desktop" | "mobile";
}) {
  const pathname = usePathname();

  return (
    <nav
      aria-label={location === "mobile" ? "Primary mobile" : "Primary"}
      className={location === "mobile" ? "bottom-navigation" : "top-navigation"}
    >
      {items.map((item) => {
        const active = pathname.startsWith(item.href);
        return (
          <Link aria-current={active ? "page" : undefined} href={item.href} key={item.href}>
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
