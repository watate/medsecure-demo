import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  images: {
    remotePatterns: [
      { hostname: "images.unsplash.com" },
    ],
  },
};

export default nextConfig;
