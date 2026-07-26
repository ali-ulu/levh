# LEVH Installation

## Quick Start (5 minutes)

### 1. Install

```bash
# Clone the repo
git clone https://github.com/ali-ulu/levh.git
cd levh

# Install in editable mode (recommended for development)
pip install -e .

# Or install as a package
pip install .
```

### 2. Verify

```bash
levh doctor
```

Expected output:

```
  LEVH Doctor
  ==================================================
  Python                    PASS   3.12.x
  Package import            PASS
  Database path             PASS   /path/to/levh
  Embedder mode             PASS   hash (default)
  API import                PASS
  MCP import                PASS
  MCP SSE import            PASS
  Frontend dir              PASS   /path/to/levh/frontend
  Config generator          PASS
  Env vars                  PASS   Defaults used

  Verdict: OK
```

### 3. Initialize

```bash
levh init
```

Creates `.stackmemory/config.json` with sensible defaults:

```json
{
  "embedder_mode": "hash",
  "database_path": "stackmemory.db",
  "api_host": "127.0.0.1",
  "api_port": 8000,
  "mcp_transport": "stdio"
}
```

### 4. Run the server

```bash
levh serve
# or with auto-reload for development
levh serve --reload
```

The API is available at `http://127.0.0.1:8000`. The dashboard frontend can be built and served separately (see Frontend section below).

## Frontend Build

```bash
cd frontend
npm install
npm run build
npm start
```

The frontend is a Next.js application. For development with hot reload:

```bash
cd frontend
npm run dev
```

## Embedder Modes

LEVH supports three embedding modes, controlled by the `EMBEDDER_MODE` environment variable:

| Mode | Command | Requirements | Quality |
|------|---------|-------------|---------|
| `hash` | `EMBEDDER_MODE=hash levh serve` | None (built-in) | Deterministic, non-semantic |
| `local` | `EMBEDDER_MODE=local levh serve` | `pip install -e ".[local]"` + torch | Good semantic similarity |
| `openai` | `EMBEDDER_MODE=openai levh serve` | `OPENAI_API_KEY` env var | Best quality, requires API key |

For quick demos and testing, `hash` mode works with zero additional dependencies.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDER_MODE` | `hash` | Embedding mode (hash / local / openai) |
| `SQLITE_DB_PATH` | `./stackmemory.db` | Path to SQLite database file |
| `SHORT_TERM_MAX` | `50` | Max short-term memories (FIFO) |
| `OPENAI_API_KEY` | — | Required for openai embedder mode |

## Troubleshooting

### "Package import: FAIL"

Make sure you are in the project root and the `server/` directory exists:

```bash
ls server/
pip install -e .
```

### "Database path: FAIL"

Check that the directory for `SQLITE_DB_PATH` exists and is writable:

```bash
mkdir -p $(dirname $SQLITE_DB_PATH)
```

### "MCP import: FAIL"

Install MCP dependencies:

```bash
pip install mcp fastmcp
```

### Port already in use

```bash
levh serve --port 8001
```
