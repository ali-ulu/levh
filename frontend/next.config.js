/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: `npm run build` emits frontend/out/, which the FastAPI
  // server serves at / — one process, one port, zero frontend infra.
  output: "export",
  // Static export (`output: "export"`) never runs file-tracing, so no tracing
  // guard is needed. (The old top-level `outputFileTracing: false` key is not
  // recognized by Next 15.5 and emitted a warning; removed.)
  trailingSlash: true,
  images: { unoptimized: true },
  // Keep static export deterministic and avoid fork/pipe instability in
  // constrained CI and release environments.
  experimental: { cpus: 1 },
};

module.exports = nextConfig;
