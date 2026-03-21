import path from "path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  // Suppress workspace root detection warning
  outputFileTracingRoot: path.join(__dirname, "../"),
};

export default nextConfig;
