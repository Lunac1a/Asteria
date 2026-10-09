import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: { proxyTimeout: 120_000, proxyClientMaxBodySize: "11mb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.ASTERIA_API_URL ?? "http://127.0.0.1:18001"}/api/:path*` }];
  },
};

export default nextConfig;
