/** @type {import('next').NextConfig} */
const nextConfig = {
  // Anchor the project root to this directory. Without this, Next.js walks up
  // looking for a lockfile to infer the workspace root and can pick a stray
  // package-lock.json in the user's home directory, which silently breaks
  // every "@/*" alias import with "Module not found". CI/Docker are unaffected
  // (no home lockfile there), but local builds hit it.
  outputFileTracingRoot: __dirname,
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
