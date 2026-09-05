# Configuration

Runtime settings use one precedence order across CLI, API, MCP and generated
client configs:

```text
explicit CLI/API override > environment > .stackmemory/config.json > defaults
```

`levh init` and `levh setup` create `.stackmemory/config.json`.
Relative database paths in that file are resolved from the working directory.
LEVH does not load `.env` implicitly; export environment variables in
the process that launches it when environment overrides are required.

| Variable | Default | Description |
|----------|---------|-------------|
| `SQLITE_DB_PATH` | `./stackmemory.db` | SQLite database path |
| `EMBEDDER_MODE` | `auto` | `auto`, `local`, `openai`, `ollama`, `hash`; `auto` is local-first and never selects OpenAI just because a key exists |
| `OPENAI_API_KEY` | — | Credential only. Its presence never enables any outbound call on its own — `EMBEDDER_MODE`, `ANSWER_MODE` and `SUMMARY_MODE` decide that |
| `ANSWER_MODE` | `auto` (offline) | Set to `llm` to let Ask synthesize answers via OpenAI. Unset means Ask is fully offline |
| `SUMMARY_MODE` | `auto` (offline) | Set to `llm` to let session summaries, consolidation and transcript ingest call OpenAI. Unset means all three are fully offline |
| `LOCAL_MODEL` | `all-MiniLM-L6-v2` | Local embedding model |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server (mode `ollama`) |
| `OLLAMA_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `SHORT_TERM_MAX` | `50` | Max short-term memories |
| `DECAY_HALF_LIFE_HOURS` | `168` | Starting half-life for new memories |
| `HSCORE_ALPHA` | `0.4` | Similarity weight |
| `HSCORE_BETA` | `0.2` | Decay weight |
| `HSCORE_GAMMA` | `0.3` | Importance weight |
| `HSCORE_DELTA` | `0.1` | Frequency weight |
| `REINFORCEMENT_GAIN` | `0.5` | Stability growth per recall (higher = faster consolidation) |
| `MAX_STABILITY_HOURS` | `8760` | Cap on how durable a memory can become (1 year) |
| `FEEDBACK_WEAKEN_FACTOR` | `0.5` | Stability multiplier on negative feedback |
| `INTERFERENCE_THRESHOLD` | `0.97` | Similarity above which new memories weaken old ones (1.0 = off) |
| `INTERFERENCE_FACTOR` | `0.6` | Stability multiplier applied to superseded memories |
| `AUTO_SUMMARIZE_SESSIONS` | `false` | Auto-summarize a session's memories on `end_session` |
| `SUMMARY_MODEL` | `gpt-4o-mini` | OpenAI chat model used for session summaries |
| `OPENAI_BASE_URL` | OpenAI's endpoint | OpenAI-compatible chat completions URL. Point it at a local server (Ollama, LM Studio, vLLM) to summarize and chat fully offline |
| `LEVH_LIBRARIAN` | `1` | Librarian watchdog: periodically scans which agents on this machine are wired to levh and how memory is being used. `0`/`false`/`off` disables it |
| `LEVH_LIBRARIAN_INTERVAL` | `600` | Seconds between librarian scans |
| `LEVH_LIBRARIAN_SHELL` | `1` | Lets the librarian run a shell command the LLM proposes. The destructive-command filter is a blocklist, so it stops known patterns, not all of them — set `0` to remove the capability |
| `LEVH_AUTO_CHECKPOINT_INTERVAL` | `600` | Seconds between automatic checkpoints on the MCP server side |
| `LEVH_TOKEN` | — | Shared-secret gate required for non-loopback access unless an external boundary is explicitly declared |
| `LEVH_ALLOW_REMOTE_WITHOUT_TOKEN` | `false` | Advanced operator assertion that an external network boundary protects tokenless non-loopback traffic; never use with a public port |
| `LEVH_CORS_ORIGINS` | localhost only | Comma-separated allowed browser origins (`*` for wildcard) |
| `LEVH_AUTH_RATE_LIMIT` | `10` | Failed token attempts allowed per rate-limit window, per client/process |
| `LEVH_API_RATE_LIMIT` | `120` | Authenticated API requests allowed per window, per client/process |
| `LEVH_RATE_LIMIT_WINDOW_SECONDS` | `60` | In-process rate-limit window; not a distributed quota system |
| `LEVH_SQLITE_BUSY_TIMEOUT_MS` | `5000` | SQLite lock wait before failing; file databases use WAL mode |
| `LEVH_SAFETY_BACKUP_DIR` | DB sibling `safety-backups/` | Location for automatic pre-replace SQLite safety backups |

---

## Docker

```bash
docker compose up -d
# Dashboard + API: http://localhost:8000
# MCP SSE stream: http://localhost:8000/api/mcp/sse
```

One container, one port. The image builds the dashboard and serves it from the API.

Compose explicitly accepts tokenless Docker-bridge traffic because the published
host port is restricted to `127.0.0.1` — inside the container the host's traffic
arrives from the bridge gateway, which is not a loopback peer. If that port
mapping is widened, remove `LEVH_ALLOW_REMOTE_WITHOUT_TOKEN` and set a strong
`LEVH_TOKEN` instead.
