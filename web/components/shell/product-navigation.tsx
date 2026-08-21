"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Icon } from "@/components/ui/icons";
import { previewNavigationItems, type NavigationItem } from "@/lib/navigation";

export function ProductNavigation({
  location,
  items = previewNavigationItems,
}: {
  location: "desktop" | "mobile";
  items?: readonly NavigationItem[];
}) {
  const pathname = usePathname();

  return (
    <nav
      aria-label={location === "mobile" ? "Primary mobile" : "Primary"}
      className={location === "mobile" ? "bottom-navigation" : "top-navigation"}
    >
      {items.map((item) => {
        const active = pathname.startsWith(item.match ?? item.href);
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
