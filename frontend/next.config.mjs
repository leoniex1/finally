/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: `npm run build` writes a fully static site to frontend/out/,
  // which the FastAPI container serves. No Node server at runtime.
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
