import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
    // One entry per TMDB size the product actually renders, rather than a
    // wildcard over `/t/p/**`: the optimizer will fetch anything this list
    // allows, so it stays the list of shapes we asked for — w500 posters,
    // w1280 detail backdrops, w185 cast portraits.
    remotePatterns: [
      {
        protocol: "https",
        hostname: "image.tmdb.org",
        port: "",
        pathname: "/t/p/w500/**",
        search: "",
      },
      {
        protocol: "https",
        hostname: "image.tmdb.org",
        port: "",
        pathname: "/t/p/w1280/**",
        search: "",
      },
      {
        protocol: "https",
        hostname: "image.tmdb.org",
        port: "",
        pathname: "/t/p/w185/**",
        search: "",
      },
    ],
  },
};

export default nextConfig;
