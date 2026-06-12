import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["*.trycloudflare.com"],
  outputFileTracingExcludes: {
    "*": ["agents/**"],
  },
};

export default nextConfig;
