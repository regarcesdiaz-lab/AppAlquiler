import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Fully static site: `next build` writes plain HTML/CSS/JS to `out/`.
  output: "export",
  images: { unoptimized: true },
  // cacheComponents / partialPrefetching (template defaults) rely on PPR, which static export
  // does not support.
};

export default nextConfig;
