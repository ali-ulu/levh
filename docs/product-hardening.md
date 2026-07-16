# StackMemory Product Hardening

## What works today (v2.3)

- **Human-memory model**: every memory has its own half-life (stability) measured from last access; recall reinforces it (spaced-repetition), importance accelerates consolidation, `memory_feedback` weakens wrong/stale facts, near-identical new memories weaken superseded old ones (retroactive interference), and a fading review queue surfaces what's about to be forgotten
- **Memory storage**: content, importance, tags, session, **project**, **source**, **pinned**, metadata
- **Semantic recall**: H(x,psi) scored recall; filters (session/project/importance) applied *before* ranking; only returned memories get their access frequency bumped
- **3-layer memory**: Short-term (FIFO), Episodic (SQLite), VectorStore (NumPy cosine, mixed-dimension safe)
- **One shared engine**: REST, WebSocket, MCP stdio, and MCP SSE all operate on the same engine instance — no split-brain state
- **Env-configurable scoring**: HSCORE_ALPHA..DELTA and DECAY_HALF_LIFE_HOURS are read from the environment
- **Pinning**: pinned memories never decay, sort first, are never auto-deduped
- **Projects & sources**: per-workspace namespacing and per-AI-client attribution, with dashboard facets
- **Context files**: compile memories into CLAUDE.md / .cursorrules (UI, CLI `stackmemory context`, MCP tool)
- **Auto-capture**: git post-commit hook (`stackmemory hook install`) + `stackmemory capture` CLI
- **Dedupe**: embedding-similarity duplicate detection with dry-run
- **Embedders**: openai / local (sentence-transformers) / **ollama** / hash fallback — never crashes
- **Schema migration**: v1 databases upgrade in place (ALTER TABLE on connect)
- **Dashboard**: served by the API itself (single process/port), live WebSocket activity feed, semantic search with score explanations, full CRUD, insights charts, MCP setup snippets, connector imports, export/import
- **REST API**: 35 endpoints; **MCP**: 29 tools
- **Auto-capture**: session summarization on `end_session` (LLM or offline extractive) — `AUTO_SUMMARIZE_SESSIONS`
- **Related memories**: live nearest-neighbour "see also" graph edge (`get_related`, REST + MCP)
- **Recall benchmark**: `scripts/benchmark_recall.py` reports hit@k / MRR
- **Tests**: 122 passing (`EMBEDDER_MODE=hash python -m pytest -q`); GitHub Actions CI (backend tests + frontend build)
- **Docker**: single image (builds dashboard + API), single compose service

## Known gaps (not production-ready as SaaS)

- No multi-tenant / per-user auth — designed as a local, single-user tool.
  (An optional single shared-secret gate exists via `STACKMEMORY_TOKEN`, and
  CORS now defaults to localhost origins instead of a wildcard.)
- No cloud sync / team sharing
- Hash embedder is non-semantic (fine for demos/tests; use local/ollama/openai for real use)
- WebSocket event fan-out is in-process only (fine for single-server deployments)

## Next steps

### A — Distribution
`uvx stackmemory` / PyPI publish; one-line installer that writes MCP configs for detected clients.

### B — Deeper auto-capture
Session summarization on `end_session` — **done** (`summarize_session`, LLM +
extractive fallback). Remaining: capture from shell history; IDE save hooks.

### C — Memory intelligence
Related-memory edges (`get_related`) and recall trace (score-breakdown) —
**done**. Remaining: LLM-powered compress of *old* memories; explicit relation
extraction (typed edges) between memories.

### D — Sync (SaaS option)
Optional encrypted sync server; per-seat teams; memory sharing between machines. Local-first stays the default.
