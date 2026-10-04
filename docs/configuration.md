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

These are the process's runtime settings. The environment a *developer* runs the
suite in is a separate contract, and the only supported one is the locked `uv`
environment documented in
[Testing → the development environment](testing.md#the-development-environment).
Testing through a `python` outside that environment can fail at import in a way
that reads like a broken change.

| Variable | Default | Description |
|----------|---------|-------------|
| `SQLITE_DB_PATH` | `./stackmemory.db` | SQLite database path (canonical alias `LEVH_SQLITE_DB_PATH` also accepted) |
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
| `SUMMARY_MODEL` | `gpt-4o-mini` | Chat model used for session summaries **and** the librarian's chat — one variable, one default, so the two cannot disagree |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. A base (`http://localhost:11434/v1` for Ollama, LM Studio, vLLM) or the full `/chat/completions` path both work |
| `LEVH_LIBRARIAN` | `1` | The librarian watcher. `0`/`false`/`off` stops it from starting; the `/api/librarian/*` routes remain served |
| `LEVH_LIBRARIAN_INTERVAL` | `600` | Seconds between watcher scans |
| `LEVH_AUTO_CHECKPOINT_INTERVAL` | `600` | Seconds between automatic checkpoints on the MCP server side |
| `LEVH_TOKEN` | — | Shared-secret gate required for non-loopback access unless an external boundary is explicitly declared |
| `LEVH_ENABLE_API_DOCS` | `false` when a token is set | Serve `/docs`, `/redoc` and `/openapi.json`. These are withheld while `LEVH_TOKEN` is set — a browser cannot attach the token header to the docs page itself, so the whole route map would otherwise be anonymous. Set to `true` only on a trusted network |
| `LEVH_ALLOW_REMOTE_WITHOUT_TOKEN` | `false` | Advanced operator assertion that an external network boundary protects tokenless non-loopback traffic; never use with a public port. `levh doctor` fails when it is combined with a non-loopback bind, and `/api/health` reports both `unauthenticated_remote_access` and the `api_host` the process is actually bound to, for as long as the override is in effect |
| `LEVH_DOCTOR_NO_LIVE_PROBE` | `false` | Test isolation for `levh doctor`: when set, the live-server probe is skipped and the remote-access check reads argv/config only. An unrelated server on a fallback port would otherwise answer the probe and darken the verdict |
| `LEVH_CORS_ORIGINS` | localhost only | Comma-separated allowed browser origins (`*` for wildcard) |
| `LEVH_MAX_REQUEST_BODY_BYTES` | `16777216` (16 MB) | Cap on a mutating request body (`POST`/`PUT`/`PATCH`). Enforced against both the declared `Content-Length` and the streamed body, so a chunked or lying request is still refused with `413`. `/api/connectors/upload` is exempt and keeps its own 64 MB decoded file limit |
| `LEVH_AUTH_RATE_LIMIT` | `10` | Failed token attempts allowed per rate-limit window, per client/process |
| `LEVH_API_RATE_LIMIT` | `120` | Authenticated API requests allowed per window, per client/process |
| `LEVH_RATE_LIMIT_WINDOW_SECONDS` | `60` | In-process rate-limit window; not a distributed quota system |
| `LEVH_SQLITE_BUSY_TIMEOUT_MS` | `5000` | SQLite lock wait before failing; file databases use WAL mode |
| `LEVH_SAFETY_BACKUP_DIR` | DB sibling `safety-backups/` | Location for automatic pre-replace SQLite safety backups |
| `LEVH_CONFIG_PATH` | `<cwd>/.stackmemory/config.json` | Redirect where the JSON config file is read from |
| `LEVH_SYNONYMS_PATH` | packaged vocabulary | JSON file of equivalent terms used to expand recall queries (see below). Unset means `server/data/synonyms.json`, shipped with the package; set it to use a store's own vocabulary |
| `LEVH_PUBLIC_DEMO` | `false` | When `true`, mutating API calls are refused so the process can serve a read-only public instance |
| `LEVH_MCP_PROFILE` | `full` | MCP tool surface: `minimal`, `work`, `admin` or `full`. Written into generated client configs by `levh mcp config --profile <name>` |
| `LEVH_AGENT` | — | Agent identity recorded with presence/heartbeat rows. Falls back to `AGENT_NAME`, `CLAUDE_AGENT`, `CURSOR_AGENT`, then `auto-connect` |
| `LEVH_AUTO_HEARTBEAT` | `1` | Auto-heartbeat while an MCP stdio session is open. `0`/`false`/`no` disables it |
| `LEVH_AUTO_CONNECT` | `1` | MCP stdio startup: detect the agent/project and open a session. `0` disables it |
| `LEVH_AUTO_BRIEF` | `1` | MCP stdio startup: print the continuity brief to stderr for every client. `0` disables it |
| `LEVH_AUTO_CHECKPOINT` | `0` | MCP stdio startup: fold new memories into a checkpoint periodically. Off unless set |
| `LEVH_DASHBOARD_DIR` | — | Override the built dashboard static export directory (source checkouts use `frontend/out`, wheels use `server/dashboard`) |
| `LEVH_EMBEDDER_DEBUG` | — | Append the underlying embedder exception to the hash-fallback reason |
| `LEVH_VERSION` | `unknown` | Version reported when package metadata is unavailable |
| `LEVH_DOGFOOD_ENABLED` | `false` | Append whitelisted aggregate dogfood events to a local JSONL file; no content leaves the process |
| `LEVH_ONBOARDING_RECEIPT_PATH` | `.stackmemory/onboarding-receipt.json` | Where the local, privacy-safe onboarding receipt is written |
| `LEVH_RECALL_LOG` | `true` | Record every recall in `recall_log`: the query (secrets redacted, truncated at 1000 characters) and the ranked result ids the recall returned. This is the substrate `levh recall-report` measures; `0`/`false`/`off` opts out. Memory content, scores and embeddings are never stored |
| `LEVH_RECALL_LOG_DAYS` | `30` | Days of recall history to keep. Retention runs on the recall path at most once every ten minutes, so an active store bounds itself without a scheduler. `0` disables pruning |

---

## Query synonyms

Model-free recall (`EMBEDDER_MODE=hash`) ranks by word overlap, so a memory
written as "API authentication uses JWT tokens" does not match the query "how
do users log in" — no word is shared. A synonyms file supplies the missing
equivalences: each group is one idea written several ways, and a query term
expanded through it can reach the memory.

```json
{
  "groups": [
    ["login", "signin", "log in", "authentication", "auth"],
    ["deploy", "deployment", "release"],
    ["migration", "migrate", "migrasyon"]
  ]
}
```

Point `LEVH_SYNONYMS_PATH` at the file. Without it, the vocabulary shipped with
the package (`server/data/synonyms.json`) is used, so expansion works out of
the box. The file is re-read when its mtime changes, so editing it takes effect
without restarting the process. A map shape (`{"login": ["signin"]}`) is
accepted too, and a malformed file is logged and ignored rather than failing
recall.

Expansion is retrieval-only; it never widens the duplicate check, since two
memories that use different words for the same idea are not duplicates.

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

That boundary no longer rests on the comment alone. `levh doctor` fails when the
override is set while the server binds a non-loopback address, and the running
server reports both `unauthenticated_remote_access: true` and its own
`api_host` from `/api/health` for as long as the override is in effect — so
widening the publish without adding a token is caught rather than silently
exposed.

The bind address is read from what the running process was actually told to
bind, in this order: the address a live server reports on `/api/health`, then
`--host` in argv, then `API_HOST`, then the config file, then the
`127.0.0.1` default. Config alone would not be enough: the image's
`uvicorn --host 0.0.0.0` and `levh serve --host 0.0.0.0` both bind an address
the config file never mentions, so reporting config would describe a server
that is not the one running.
