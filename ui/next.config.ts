import path from "path";
import type { NextConfig } from "next";

function extractHost(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    return new URL(trimmed).hostname;
  } catch {
    return null;
  }
}

const envOriginHosts = (process.env.WEB_CORS_ORIGINS ?? "")
  .split(",")
  .map(extractHost)
  .filter((h): h is string => Boolean(h));

const allowedDevOrigins = Array.from(
  new Set(["localhost", "127.0.0.1", "192.168.88.139", ...envOriginHosts]),
);

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  allowedDevOrigins,
  images: {
    unoptimized: true,
  },
  // Suppress workspace root detection warning
  outputFileTracingRoot: path.join(__dirname, "../"),
};

export default nextConfig;
