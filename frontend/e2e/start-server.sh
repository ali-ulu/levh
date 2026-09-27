#!/usr/bin/env bash
# Boot a real LEVH server for the E2E suite.
#
#   start-server.sh <port> <db-path> [token]
#
# The dashboard is the Next.js static export in frontend/out, which the
# FastAPI server serves at "/". So the tests exercise one process on one port:
# the same wiring a user gets from `levh serve`, with no dev server, no proxy
# and no CORS shim standing in for it.
set -euo pipefail

PORT="$1"
DB="$2"
TOKEN="${3:-}"

cd "$(dirname "$0")/.." # frontend/

# Rebuild unless the export is complete. Checking only for index.html is not
# enough: the release commits the HTML shells under frontend/out/ but leaves
# `_next/` (gitignored) out of the tree, so a fresh clone has index.html that
# references bundle hashes no process can serve. `levh serve` then answers "/"
# with 200 and every chunk with 404 — a blank dashboard. Requiring the `_next`
# directory is what distinguishes a usable export from those orphaned shells.
if [ ! -f out/index.html ] || [ ! -d out/_next ]; then
  echo "[e2e] frontend/out is missing or incomplete — building the static export" >&2
  NEXT_TELEMETRY_DISABLED=1 npm run build >&2
fi

cd ..

# A fresh temp database per server keeps the suite order-independent and stops
# it from ever reading or writing the developer's real store. The LEVH_-prefixed
# spelling outranks the plain one, so it wins over anything already exported.
export LEVH_SQLITE_DB_PATH="$DB"
export EMBEDDER_MODE=hash
export LEVH_LIBRARIAN=0
export NEXT_TELEMETRY_DISABLED=1
if [ -n "$TOKEN" ]; then
  export LEVH_TOKEN="$TOKEN"
fi

exec uv run --frozen levh serve --host 127.0.0.1 --port "$PORT"
