# StackMemory 2.3.1 Release Blocker Fix Report

## Scope

Must-have release blocker fixes only. No new product feature, no HUQAN bridge, no cloud/auth/billing/workspace, no auto-capture expansion.

## Implemented

- Moved recall benchmark runtime code from non-package `scripts/` into packaged `server.core.benchmark`.
- Kept `scripts/benchmark_recall.py` as a thin source-tree wrapper.
- Updated CLI, API, and tests to import benchmark runtime from `server.core.benchmark`.
- Packaged static dashboard export under `server/dashboard` and added package-data/MANIFEST rules.
- Updated API dashboard serving to use, in order: `STACKMEMORY_DASHBOARD_DIR`, source `frontend/out`, packaged `server/dashboard`.
- Fixed MCP SSE mount so public stream path is `/api/mcp/sse` instead of `/api/mcp/sse/sse`.
- Added explicit embedder requested/effective mode reporting and actionable fallback warning for missing local embedder dependencies.
- Upgraded frontend stack to Next.js 15.5.20 / React 19.1.1 and pinned PostCSS override to eliminate `npm audit --omit=dev` findings.
- Added `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `.dockerignore`, `MANIFEST.in`, and `docs/public-launch-checklist.md`.
- Added packaging and embedder-mode regression tests.

## Validation run

Backend/source validation:

- `python -m compileall -q server tests`: PASS
- `EMBEDDER_MODE=hash python tests/test_api_smoke.py`: PASS, exit 0
- Pytest split due tool execution window:
  - `tests/test_api_smoke.py`: 2 passed
  - `tests/test_cli.py::TestDoctor tests/test_cli.py::TestInit`: 7 passed
  - `tests/test_cli.py::TestMcpConfig tests/test_cli.py::TestBenchmark tests/test_cli.py::TestSummarize`: 11 passed
  - `tests/test_integration.py`: 43 passed
  - `tests/test_review_fixes.py tests/test_score_breakdown_api.py tests/test_v2_features.py tests/test_packaging.py tests/test_embedder_modes.py`: 68 passed
  - Total: 131 passed

Packaging validation:

- `python -m build`: PASS
- `twine check dist/*`: PASS
- Wheel contains `server/core/benchmark.py`: PASS
- Wheel contains `server/dashboard/index.html`: PASS
- Installed wheel `stackmemory benchmark`: PASS
- Installed wheel `GET /` dashboard HTML via FastAPI TestClient: PASS

Frontend validation:

- `npm ci --legacy-peer-deps`: PASS
- `NEXT_TELEMETRY_DISABLED=1 npm run build`: PASS, exit 0
- `npm audit --omit=dev`: PASS, 0 vulnerabilities

CLI validation:

- `stackmemory doctor`: PASS
- `stackmemory init --force`: PASS
- `stackmemory mcp config claude/cursor/windsurf`: PASS, valid JSON

Docker validation:

- Not run: Docker unavailable in environment.

## Remaining risks

- `next.config.js` currently keeps `outputFileTracing: false` as a static-export build guard. Next.js 15.5.20 warns that this key is unrecognized, but the build exits 0. Removing it caused trace-collection hangs in this environment.
- Docker build still needs a real Docker host validation.
- MCP SSE endpoint path is corrected, but full protocol-level SSE client smoke remains recommended before public SSE claims.
- Built dashboard static files are packaged under `server/dashboard`; regenerate that copy whenever frontend changes.
