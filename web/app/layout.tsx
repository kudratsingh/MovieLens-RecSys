import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "MovieLens", template: "%s · MovieLens" },
  description:
    "Explore a tenant-aware two-stage movie recommender and the ML system behind every result.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
