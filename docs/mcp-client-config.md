# MCP Client Configuration

StackMemory provides an MCP (Model Context Protocol) server that can be connected to various AI coding clients. The CLI generates ready-to-use JSON configs.

## Supported Clients

| Client | CLI Command | Config File |
|--------|------------|-------------|
| Claude Desktop | `stackmemory mcp config claude` | `claude_desktop_config.json` |
| Claude Code | `stackmemory mcp config claude_code` | `.claude.json` |
| Cursor | `stackmemory mcp config cursor` | `.cursor/mcp.json` |
| Windsurf | `stackmemory mcp config windsurf` | `.windsurf/mcp.json` |
| VS Code + Cline | `stackmemory mcp config vscode` | `.vscode/mcp.json` |
| Generic | `stackmemory mcp config generic` | (stdout JSON) |

## Claude Desktop

### Generate config

```bash
stackmemory mcp config claude
```

Output:

```json
{
  "mcpServers": {
    "stackmemory": {
      "command": "python",
      "args": ["-m", "server.mcp_stdio"],
      "cwd": "/path/to/stackmemory",
      "env": {
        "EMBEDDER_MODE": "hash",
        "SQLITE_DB_PATH": "/path/to/stackmemory/stackmemory.db"
      }
    }
  }
}
```

### Install

1. Run `stackmemory mcp config claude > ~/path/to/claude_desktop_config.json`
   (merge with existing content if the file already has other servers)
2. Restart Claude Desktop
3. Verify: in Claude Desktop, look for the hammer icon — StackMemory tools should appear

### Custom options

```bash
stackmemory mcp config claude --embedder-mode local --db-path ./my-memories.db
```

## Cursor IDE

### Generate config

```bash
stackmemory mcp config cursor
```

### Install

Copy the output to `.cursor/mcp.json` in your project root:

```bash
stackmemory mcp config cursor > .cursor/mcp.json
```

Restart Cursor. The StackMemory tools appear in the Cursor agent panel.

## Windsurf

### Generate config

```bash
stackmemory mcp config windsurf
```

### Install

Copy the output to `.windsurf/mcp.json`:

```bash
mkdir -p .windsurf
stackmemory mcp config windsurf > .windsurf/mcp.json
```

## Claude Code (CLI)

### Generate config

```bash
stackmemory mcp config claude_code
```

### Install

```bash
stackmemory mcp config claude_code > .claude.json
```

## VS Code + Cline Extension

### Generate config

```bash
stackmemory mcp config vscode
```

### Install

```bash
mkdir -p .vscode
stackmemory mcp config vscode > .vscode/mcp.json
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
- **get_context** — Get full context for a session
- **session_management** — Create/end/list sessions
- **export_import** — Export or import memory data

## MCP Stdio Server (Manual Launch)

For testing or custom integrations, launch the stdio server directly:

```bash
stackmemory mcp stdio
```

Or equivalently:

```bash
python -m server.mcp_stdio
```

## MCP SSE Server (Web Clients)

For web-based clients, the SSE transport is available:

```bash
uvicorn server.mcp_sse:app --host 0.0.0.0 --port 8001
```

Or via the FastAPI mount at `/api/mcp/sse` when using the main API server.

## Troubleshooting

### "stackmemory: command not found"

Install the package first:

```bash
pip install -e .
```

### Claude Desktop shows "Failed to connect"

1. Verify the `cwd` path in the config points to the actual StackMemory project root
2. Run `stackmemory doctor` to check all dependencies
3. Make sure the Python executable matches the one used for installation

### Tools not appearing in Cursor

1. Check that `.cursor/mcp.json` is valid JSON
2. Restart Cursor completely
3. Verify with `stackmemory mcp config cursor` that output is correct

### Embedder errors in production

For production use, switch from `hash` to a real embedder:

```bash
# Local (requires torch)
pip install -e ".[local]"
stackmemory mcp config claude --embedder-mode local

# Or OpenAI
export OPENAI_API_KEY=sk-...
stackmemory mcp config claude --embedder-mode openai
```
