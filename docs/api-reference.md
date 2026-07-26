# REST API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/memories` | Store a memory |
| GET | `/api/memories` | List with filters (`q`, `project`, `source`, `tag`, `pinned`, ...) |
| GET | `/api/memories/{id}` | Get single memory |
| PUT | `/api/memories/{id}` | Update memory |
| PATCH | `/api/memories/{id}/pin` | Pin / unpin |
| POST | `/api/memories/{id}/reinforce` | Manually strengthen a memory |
| POST | `/api/memories/{id}/feedback` | helpful=true/false — learn from recall outcomes |
| GET | `/api/memories/fading` | Memories about to be forgotten (review queue) |
| DELETE | `/api/memories/{id}` | Delete memory |
| POST | `/api/memories/recall` | Recall by query (optional project filter) |
| POST | `/api/memories/consolidate` | Short-term → episodic |
| POST | `/api/memories/dedupe` | Find or remove near-duplicates |
| POST | `/api/memories/export` | Export all as JSON |
| POST | `/api/memories/import` | Import from JSON |
| GET | `/api/memories/{id}/score-breakdown` | Explain a memory's H(x,ψ) score |
| GET | `/api/memories/{id}/forgetting-curve` | Predicted retention curve over time |
| GET | `/api/memories/{id}/related` | Nearest-neighbour "see also" memories |
| POST | `/api/sessions` | Create session |
| GET | `/api/sessions` | List sessions |
| GET | `/api/sessions/{id}` | Get session |
| PATCH | `/api/sessions/{id}/end` | End session (consolidates) |
| POST | `/api/sessions/{id}/summarize` | Distill a session into one summary memory |
| GET | `/api/projects` | Projects with counts |
| GET | `/api/sources` | AI clients with counts |
| GET | `/api/tags` | Tags with counts |
| GET | `/api/people` | People across memories, by frequency |
| GET | `/api/people/{key}` | A person's profile + their memories |
| GET | `/api/timeline` | Memories grouped by day |
| GET | `/api/context` | Current context window |
| POST | `/api/context-file` | Generate CLAUDE.md / .cursorrules |
| GET | `/api/stats` | System statistics |
| GET | `/api/config` | Server configuration |
| GET | `/api/health` | Health check |
| POST | `/api/benchmark/recall` | Run the recall-quality benchmark (hit@k / MRR) |
| WS | `/ws/memory` | Real-time event stream + RPC actions |
| SSE | `/api/mcp/sse` | MCP SSE stream endpoint |
| POST | `/api/connectors/import` | Import from app |
| GET | `/api/connectors` | List connectors |
| GET | `/api/connectors/{name}/config` | Connector config help |
