# MCP Client Configuration

LEVH provides an MCP (Model Context Protocol) server that can be connected to various AI coding clients. The CLI generates ready-to-use JSON configs.

## Supported Clients

| Client | CLI Command | Config File |
|--------|------------|-------------|
| Claude Desktop (Anthropic) | `levh mcp config claude_desktop` | `None` |
| Claude Code (CLI) | `levh mcp config claude_code` | `.claude.json` |
| Cursor IDE | `levh mcp config cursor` | `.cursor/mcp.json` |
| Windsurf | `levh mcp config windsurf` | `.windsurf/mcp.json` |
| VS Code (with Cline extension) | `levh mcp config vscode` | `.vscode/mcp.json` |
| Cline (VS Code extension) | `levh mcp config cline` | `.vscode/mcp.json` |
| jcode (CLI) | `levh mcp config jcode` | `.jcode/mcp.json` |
| oh-my-pi / omp (CLI) | `levh mcp config omp` | `.omp/mcp.json` |
| opencode (CLI) | `levh mcp config opencode` | `opencode.json` · opencode schema |
| Codex CLI (OpenAI) | `levh mcp config codex` | `.codex/config.toml` |
| Hermes Agent (Nous Research) | `levh mcp config hermes` | `.hermes/config.yaml` |
| Generic | `levh mcp config generic` | (stdout JSON) |

Not every client reads the same shape. opencode uses its own JSON schema,
Codex expects TOML and Hermes YAML — config generated in Claude Desktop's
format is silently ignored by those three, so the generator emits each
client's own format. `tests/test_client_config_formats.py` parses the output
with the parser the platform actually uses.

## Continuity delivery inventory

This table is code-backed by `server/continuity_inventory.py`. “Server emits”
describes LEVH's stdio behavior, not proof that a particular client displays or
consumes stderr. Likewise, the MCP start directive is advertised by the server;
whether a client follows it is client behavior. The store-scale continuity-use
metrics track whether recently surfaced memories are returned by recall within
24 hours; they do not identify which client followed the directive.

<!-- continuity-inventory:start -->
| Client | Stderr bridge | Native session-start hook | MCP continuity tool/directive |
| --- | --- | --- | --- |
| Claude Desktop (Anthropic) | server emits by default | none | tool + start directive by default |
| Cursor IDE | server emits by default | none | tool + start directive by default |
| Claude Code (CLI) | server emits by default | opt-in SessionStart | tool + start directive by default |
| VS Code (with Cline extension) | server emits by default | none | tool + start directive by default |
| Windsurf | server emits by default | none | tool + start directive by default |
| Cline (VS Code extension) | server emits by default | none | tool + start directive by default |
| jcode (CLI) | server emits by default | none | tool + start directive by default |
| oh-my-pi / omp (CLI) | server emits by default | none | tool + start directive by default |
| opencode (CLI) | server emits by default | none | tool + start directive by default |
| Codex CLI (OpenAI) | server emits by default | none | tool + start directive by default |
| Hermes Agent (Nous Research) | server emits by default | none | tool + start directive by default |
| Generic MCP client | server emits by default | none | tool + start directive by default |
<!-- continuity-inventory:end -->

The native hook column means an installer-owned session-start event, not a
rules file or reminder. Today only Claude Code has that hook. Cursor's installer
also writes `.cursorrules`, and the shell helper is opt-in, but neither is a
native session-start channel.

## Claude Desktop

### Generate config

```bash
levh mcp config claude
```

Output:

```json
{
  "mcpServers": {
    "levh": {
      "command": "python",
      "args": ["-m", "server.mcp_stdio"],
      "cwd": "/path/to/levh",
      "env": {
        "EMBEDDER_MODE": "hash",
        "SQLITE_DB_PATH": "/path/to/levh/stackmemory.db"
      }
    }
  }
}
```

### Install

1. Run `levh mcp config claude > ~/path/to/claude_desktop_config.json`
   (merge with existing content if the file already has other servers)
2. Restart Claude Desktop
3. Verify: in Claude Desktop, look for the hammer icon — LEVH tools should appear

### Custom options

```bash
levh mcp config claude --embedder-mode local --db-path ./my-memories.db
```

## Cursor IDE

### Generate config

```bash
levh mcp config cursor
```

### Install

Copy the output to `.cursor/mcp.json` in your project root:

```bash
levh mcp config cursor > .cursor/mcp.json
```

Restart Cursor. The LEVH tools appear in the Cursor agent panel.

## Windsurf

### Generate config

```bash
levh mcp config windsurf
```

### Install

Copy the output to `.windsurf/mcp.json`:

```bash
mkdir -p .windsurf
levh mcp config windsurf > .windsurf/mcp.json
```

## Claude Code (CLI)

### Generate config

```bash
levh mcp config claude_code
```

### Install

```bash
levh mcp config claude_code > .claude.json
```

## VS Code + Cline Extension

### Generate config

```bash
levh mcp config vscode
```

### Install

```bash
mkdir -p .vscode
levh mcp config vscode > .vscode/mcp.json
```

## MCP Tools Available

Once connected, the following tools are available to the AI client:

- **store_memory** — Store a new memory with content, importance, tags
- **recall_memories** — Semantic search across stored memories
- **forget_memory** — Delete a specific memory
- **search_memories** — Full-text search
- **update_memory** — Edit existing memory content or metadata
- **list_memories** — List all memories with optional filters
- **get_stats** — Get memory system statistics
- **consolidate** — Move short-term memories to episodic storage
- **clear_short_term** — Clear all short-term memories
- **set_importance** — Change importance score of a memory
- **get_context** — Build the context window for a session; pass `query` to focus it on a topic within the token budget
- **session_management** — Create/end/list sessions
- **export_import** — Export or import memory data

## MCP Stdio Server (Manual Launch)

For testing or custom integrations, launch the stdio server directly:

```bash
levh mcp stdio
```

Or equivalently:

```bash
python -m server.mcp_stdio
```

## MCP SSE Server (Web Clients)

For web-based clients, the SSE transport is available:

```bash
LEVH_MCP_PROFILE=work uvicorn server.mcp_sse:app --host 127.0.0.1 --port 8001
```

Or via the FastAPI mount at `/api/mcp/sse` when using the main API server.

MCP *size* profiles (`minimal`/`work`/`admin`/`full`) limit which tools are
advertised; they are not an authentication boundary. Unset `LEVH_MCP_PROFILE`
defaults to `work` on stdio and SSE, matching the generated client configs, so
a manually launched server and a generated one advertise the same surface.

`read_only` is the exception: it is a server-enforced permission profile for
external/cloud agents that may read memory but must never write it. Use it when
the transport or the agent is outside the local trust boundary — see
[Read-only external agents](#read-only-external-agents).

### Continuous brief & auto-checkpoint (agent memory loop)

LEVH exposes `get_continuity_brief` in every MCP profile. Stdio and SSE also
publish a server `instructions` directive telling compatible clients to call
that tool at the start of a work session. Separately, the stderr bridge can emit
the brief itself at startup, and auto-checkpoint can fold a working session's
new memories into periodic summaries. Control the runtime behaviors with
environment variables on the MCP server:

| Variable | Default | Effect |
| --- | --- | --- |
| `LEVH_AUTO_CONNECT` | `1` | Auto-detect agent + project (env / git remote), create a tracking session, start the heartbeat loop. Set `0` to disable. |
| `LEVH_AUTO_BRIEF` | `1` | Print the continuity brief to the server's stderr at startup, so terminal-side MCP clients (Claude Code, Codex, …) receive it without calling a tool. Set `0` to disable. This does not disable the protocol-level server instruction; that carries only the directive, not memory content. |
| `LEVH_AUTO_CHECKPOINT` | `0` | Enable the background auto-checkpoint loop (`1`/`true`). |
| `LEVH_AUTO_CHECKPOINT_INTERVAL` | `300` | Seconds between auto-checkpoints (minimum sensible value ~60). |

The auto-checkpoint loop is **delta-based and non-repeating**: each run folds
only memories created since the last checkpoint, summarizes them, and skips the
write entirely when nothing is new — so consecutive summaries never repeat the
same boilerplate. The summary uses the offline extractive path unless
`SUMMARY_MODE=llm` is set.

The same logic powers the interactive scheduler:

```bash
levh checkpoint auto --interval 300    # summarize new memories every 5 min
```

For CLI agents, `levh brief` is an opt-in shell helper that prints the brief on
demand (idempotent installer, never runs on its own):

```bash
levh hook install --client shell      # adds `levh brief` (bash/zsh) / `levh-brief` (PowerShell)
levh hook uninstall --client shell
```
Keep the standalone server on loopback unless remote access is intentional.
Before any non-loopback bind, set a strong `LEVH_TOKEN`. Header-capable clients
must include it in `X-LEVH-Token` on every SSE transport request (the legacy
`X-StackMemory-Token` header is also accepted):

```bash
LEVH_TOKEN='replace-with-a-strong-random-token' \
LEVH_MCP_PROFILE=work \
uvicorn server.mcp_sse:app --host 0.0.0.0 --port 8001
```

`LEVH_ALLOW_REMOTE_WITHOUT_TOKEN=true` is an advanced operator assertion for a
deployment already protected by a trusted network boundary. It disables the
tokenless non-loopback rejection, so never use it with a public bind or rely on
a local reverse proxy unless that proxy enforces authentication itself.

Header authentication is preferred. Native browser `EventSource` cannot set
custom headers, so browser-only clients may instead use
`/sse?token=<percent-encoded-token>` and include the same query token on
`/messages/` requests. A supplied header is authoritative: a wrong header plus
a correct query token is rejected.

Uvicorn's default access log records the complete query string, including the
token. When query authentication is unavoidable, disable that log (and ensure
any reverse proxy also redacts query strings):

```bash
LEVH_TOKEN='replace-with-a-strong-random-token' \
LEVH_MCP_PROFILE=work \
uvicorn server.mcp_sse:app --host 0.0.0.0 --port 8001 --no-access-log
```

## Read-only external agents

An external or cloud agent that only needs to read memory should mount the
`read_only` profile, not `full` or `work`. It advertises the recall/search/
context/metadata reads and **no** write tools:

```bash
# stdio (the agent is a separate local process)
LEVH_MCP_PROFILE=read_only levh mcp stdio

# SSE (a remote or web client)
LEVH_TOKEN='replace-with-a-strong-random-token' \
LEVH_MCP_PROFILE=read_only \
uvicorn server.mcp_sse:app --host 0.0.0.0 --port 8001
```

This is not an advertisement trick. `server/tools/access.py` wraps the MCP
dispatcher and re-checks every `tools/call` against the read-only allowlist, so
a client that invokes a hidden write tool directly is refused with
`ToolAccessDenied` instead of executing it. Discovery and authorization agree:
`levh mcp profiles` prints the same allowlist the guard enforces.

What the profile refuses: `store_memory`, `update_memory`, `forget_memory`,
`purge_memory`, pin/importance changes, session and checkpoint writes,
consolidation/dedupe, import/export, backup/restore, connector sync/import,
agent presence writes, and the other mutating tools listed in
`MUTATING_TOOLS`. Bulk `export_memories`, `audit_secrets` and
`evaluate_admission` are admin reads and stay off the external allowlist too.

What it still writes: the server's own recall audit log (a row describing the
query and ranked ids, not a change to a memory) and derived caches such as a
trust score. Recall/search run with `reinforce=False`, the continuity brief is
built without an emission row, and startup skips auto-connect/auto-checkpoint,
so a read cannot keep a memory alive or create a session.

### Authentication semantics

| Transport | Local client | Remote client |
| --- | --- | --- |
| stdio | Inherits the OS user's filesystem access; the read-only profile is the tool boundary | Not applicable — stdio is a local process |
| standalone SSE (`server.mcp_sse:app`) | Loopback is accepted without a token when `LEVH_TOKEN` is unset | Non-loopback requires `LEVH_TOKEN`; no token and a wrong token both return `401`, a valid token returns `200` |
| `/api/mcp` mount | Protected by the main API's `LEVH_TOKEN` gate on `/api/*` | Same gate; the process profile still limits the tool surface |

`LEVH_ALLOW_REMOTE_WITHOUT_TOKEN=true` is the documented advanced override for
a deployment whose network boundary already authenticates callers; it does not
change the read-only profile's tool authority. Header authentication is
preferred; browser `EventSource` may use `?token=` because it cannot set custom
headers, and a supplied header is authoritative even when wrong.

The profile and the token are independent layers: a valid token says *who may
connect*, `read_only` says *what that connection may do*. Do not expose a
`full`-profile remote server as a shortcut, and keep the bind on loopback when
the agent runs on the same machine.

## Troubleshooting

### "levh: command not found"

Install the package first:

```bash
pip install -e .
```

### Claude Desktop shows "Failed to connect"

1. Verify the `cwd` path in the config points to the actual LEVH project root
2. Run `levh doctor` to check all dependencies
3. Make sure the Python executable matches the one used for installation

### Tools not appearing in Cursor

1. Check that `.cursor/mcp.json` is valid JSON
2. Restart Cursor completely
3. Verify with `levh mcp config cursor` that output is correct

### Embedder errors in production

For production use, switch from `hash` to a real embedder:

```bash
# Local (requires torch)
pip install -e ".[local]"
levh mcp config claude --embedder-mode local

# Or OpenAI
export OPENAI_API_KEY=sk-...
levh mcp config claude --embedder-mode openai
```
