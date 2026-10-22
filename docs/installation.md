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

### 5. Keep it running and up to date (optional)

For a machine where LEVH should just be there — without Docker or a system
service — run the supervisor from the checkout instead of `levh serve`:

```bash
cd levh
python -m server.supervisor
```

It starts `levh serve` as its child and restarts it when it exits, when the port
actively refuses connections while the process is still alive, or after ten
minutes without a health answer (a young server gets a ten-minute boot grace
first). It never kills a server it did not start: one already listening on the
port is adopted. A lock file keeps it to one copy, so it is safe to launch on a
timer. On Windows, a Scheduled Task that runs at logon and repeats every 5
minutes with `pythonw.exe -m server.supervisor` (start in the checkout) is
enough; logs go to `%LOCALAPPDATA%\LevH\supervisor.log` (elsewhere
`~/.local/state/levh/`, or `LEVH_SUPERVISOR_LOG_DIR`).

Every `LEVH_AUTO_UPDATE_HOURS` hours (default `6`, `0` disables it; the first
check runs 15 minutes after the supervisor starts) the supervisor runs
`levh update --check`. Only when the checkout is behind does it stop the server,
run `levh update`, and start the server again — stopping first because on
Windows an installer cannot replace files a running server holds open.

`levh update` only fast-forwards. It skips a checkout with local edits to
tracked files, one on another branch than `main` (`--branch` to follow
another), or one with commits upstream does not have. It reinstalls the
checkout (`pip install -e .`, or `uv pip install -e .` in a uv environment
without pip) only when `pyproject.toml` or `uv.lock` changed, and if that
install fails it moves the checkout back with `git reset --keep`, so the code on
disk always has its dependencies (the supervisor then does not retry that same
upstream commit; a newer push is tried normally). The reinstall resolves from
`pyproject.toml`, not from `uv.lock` pins; a uv-managed development checkout
should run `uv sync --frozen` by hand after a lock-only change. Git never prompts: a remote that needs
credentials makes the update fail with an error instead of waiting for input.
Run `levh update --check` to see how far behind a checkout is without touching
it; the exit code is `1` only when something needs your attention.

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

### pip is wedged after an interrupted update

An interrupted `levh update` (a killed supervisor, a reboot, a locked file) can
leave `~`-prefixed remnants in the interpreter's `site-packages` — `~evh`,
`~erver`, `~umpy`, and the like. pip reads them as invalid distributions, so
`pip install`, `pip uninstall` and `pip show` all fail in that interpreter, and
every pip command prints `Ignoring invalid distribution ~...`.

Move the remnants aside without using pip:

```bash
levh doctor --fix-debris
```

Nothing is deleted: the remnants go to a `levh-pip-debris-backup-*` directory
beside `site-packages`, so a remnant that held the only copy of something can be
recovered. `levh doctor` reports the remnants and this command even when pip
cannot run; `levh update` also clears them before it installs anything.
