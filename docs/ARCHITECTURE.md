# LEVH — Developer / Architecture Guide

A technical reference for anyone extending, embedding, or operating LEVH.
For the product pitch see `README.md`; for the maturity checklist see
`docs/product-hardening.md`.

---

## 1. What it is, in one paragraph

LEVH is a **single-process, local-first memory service** for AI coding
agents. One `MemoryEngine` coordinates three storage layers (an in-RAM
short-term deque, a SQLite episodic store, and an in-RAM NumPy vector store)
and a scoring function `H(x,ψ)` that ranks memories by *relevance × freshness ×
importance × frequency*. The same engine instance is exposed over four
transports — REST, WebSocket, MCP stdio, and MCP SSE — so every client sees
one consistent memory. Memories **decay** over time and are **reinforced** by
recall, so signal self-curates instead of accumulating noise.

---

## 2. Process & module map

```
server/
├── api.py               FastAPI app: REST + WebSocket + static dashboard + MCP SSE mount
├── mcp_stdio.py         MCP server over stdio (Claude Desktop, Cursor, …)
├── mcp_sse.py           MCP server over SSE (mounted at /api/mcp/sse)
├── cli.py               `levh` CLI: doctor / init / serve / capture / context / hook / mcp
├── configs.py           Env + config-file resolution
│
├── core/
│   ├── engine_provider.py   Process-wide singleton engine (shared by ALL transports)
│   ├── memory_engine.py     Orchestrator — the heart of the system
│   ├── types.py             Pydantic models (Memory, Session, RecallRequest, …)
│   ├── database.py          Async SQLite wrapper + schema + migrations
│   ├── episodic.py          Thin Memory<->DB mapping layer
│   ├── short_term.py        Bounded FIFO deque (live working set)
│   ├── vector_store.py      NumPy cosine-similarity search (mixed-dimension safe)
│   ├── embedder.py          openai / local / ollama / hash, with graceful fallback
│   ├── hscore.py            H(x,ψ) math: scoring, decay, reinforce, weaken, curves
│   └── summarizer.py        Session auto-capture (LLM or extractive fallback)
│
├── tools/               One file per MCP tool; register.py wires them all up
└── connectors/          Import sources: local_files / obsidian / notion / github
```

**Golden rule:** all transports resolve the engine through
`engine_provider.get_engine()`. Never construct a second `MemoryEngine` in a
request path — that would split the short-term deque and vector store.

---

## 3. Data model

A `Memory` (see `core/types.py`) is the atomic unit:

| Field | Meaning |
|---|---|
| `id` | uuid4 hex |
| `content` | the text |
| `embedding` | float[] (dimension depends on embedder; may be null) |
| `importance` | 0–1, user/agent assigned; drives reinforcement speed |
| `frequency` | access count, feeds the δ term |
| `stability_hours` | **this memory's own half-life** — grows on recall |
| `recall_count` | times reinforced |
| `accessed_at` | decay clock origin (recall resets it) |
| `pinned` | exempt from decay + interference + auto-dedupe |
| `project` / `source` / `session_id` / `tags` | namespacing & provenance |

SQLite schema, indexes, and in-place `ALTER TABLE` migrations live in
`core/database.py` (`_SCHEMA`, `_INDEXES`, `_MIGRATIONS`). Older DBs upgrade
automatically on `connect()`.

---

## 4. The memory math (`core/hscore.py`)

**Ranking** (lower = more relevant):
```
H = α·(1−similarity) + β·(1−decay) + γ·(1−importance) + δ·(1−freq_norm)
    α=0.4  β=0.2  γ=0.3  δ=0.1        (all HSCORE_* env-tunable)
```

**Decay** — per memory, measured from last access:
```
decay(t) = 0.5 ^ (hours_since_accessed / stability_hours)
```

**Reinforcement** — each recall grows durability, weighted by importance:
```
stability *= 1 + gain·(0.5 + importance)      (capped at MAX_STABILITY_HOURS)
```

**Weaken** — negative feedback / interference shrink stability (floor 1h).

Pinned memories skip decay entirely (`decay = 1.0`). This is why the
`_refresh_memory_caches` invariant (§6) matters: recall scores from cached
objects, so a pin that only hit SQLite would still be decayed at ranking time.

---

## 5. Request lifecycles

**store(content, …)**
1. validate `memory_type`, embed the content
2. add to short-term (if short_term) + vector store, persist to SQLite
3. apply **retroactive interference** — near-identical older memories in the
   same project get weakened
4. emit `stored`

**recall(query, top_k, …, reinforce=True)**
1. embed query
2. `vector_store.search` with a pre-ranking predicate (session/project/importance
   filters applied *before* top-k so filtered recalls still fill up)
3. compute `H` per candidate (pinned ⇒ decay=1.0)
4. sort ascending, take top-k
5. if `reinforce`: bump frequency, reset decay clock, grow stability, persist.
   `reinforce=False` = read-only (dashboard previews don't inflate the signal)
6. emit `recalled`

**end_session(id)** → consolidate short-term → (optional) `summarize_session`
→ mark ended → emit `session_ended`.

---

## 6. Invariants you must preserve when extending

1. **Cache coherence.** `recall()` ranks from the vector store's cached
   `Memory` objects. Any method that mutates a persisted memory's scoring
   fields (`importance`, `pinned`, `stability_hours`, …) MUST call
   `MemoryEngine._refresh_memory_caches(memory)` after persisting, or ranking
   goes stale until restart.
2. **One engine.** Resolve via `engine_provider`; don't `new` one per request.
3. **Filters before ranking.** Keep predicates in `vector_store.search`, not a
   post-top-k filter, or filtered recalls silently under-return.
4. **Mixed dimensions are legal.** The vector store only compares vectors whose
   dimension matches the query, so switching embedders never crashes recall
   (it just partitions the corpus). Don't assume a single global dimension.
5. **Fallbacks never raise.** Embedder and summarizer degrade (to hash /
   extractive) rather than throwing — a missing model must not break a store.

---

## 7. Extension points

**Add an MCP tool:** create `server/tools/<name>.py` exposing
`register(mcp, engine)` with an `@mcp.tool()` coroutine, then add two lines to
`server/tools/register.py`. (See `tools/related.py` / `tools/summarize.py` for
the smallest examples.)

**Add a REST route:** add to `api.py`. Sub-paths (`/api/memories/{id}/related`)
must be declared **before** the bare `/api/memories/{id}` catch-all — see the
existing ordering around `/api/memories/fading`.

**Add a connector:** subclass `connectors/base.py:BaseConnector`
(`connect` / `fetch` / `disconnect` + config metadata) and register it in
`connectors/__init__.py`.

**Swap the vector store:** `vector_store.py` is deliberately a small surface
(`add` / `search(predicate)` / `remove`). Replace it with Qdrant/Milvus/pgvector
by keeping that interface; nothing else in the engine needs to change.

**Swap the embedder:** add a mode to `embedder.py:embed`. Keep the
degrade-to-hash contract.

---

## 8. Configuration (env)

| Var | Default | Purpose |
|---|---|---|
| `SQLITE_DB_PATH` | `./stackmemory.db` | DB location |
| `EMBEDDER_MODE` | `auto` | `openai`/`local`/`ollama`/`hash`/`auto`; auto is local-first |
| `OPENAI_API_KEY` | — | enables embeddings only with explicit `openai` mode; may separately enable optional LLM summaries |
| `HSCORE_ALPHA…DELTA` | 0.4/0.2/0.3/0.1 | ranking weights |
| `DECAY_HALF_LIFE_HOURS` | 168 | starting half-life |
| `REINFORCEMENT_GAIN` | 0.5 | recall durability growth |
| `MAX_STABILITY_HOURS` | 8760 | durability cap (1y) |
| `FEEDBACK_WEAKEN_FACTOR` | 0.5 | negative-feedback shrink |
| `INTERFERENCE_THRESHOLD` / `_FACTOR` | 0.97 / 0.6 | supersession sensitivity |
| `AUTO_SUMMARIZE_SESSIONS` | false | auto-capture on session end |
| `LEVH_TOKEN` | — | optional shared-secret gate on `/api/*` + WS |
| `LEVH_CORS_ORIGINS` | localhost list | allowed browser origins |
| `LEVH_AUTH_RATE_LIMIT` / `LEVH_API_RATE_LIMIT` | 10 / 120 | in-process token/API limits per 60s window |
| `LEVH_SQLITE_BUSY_TIMEOUT_MS` | 5000 | lock wait; file DBs use WAL |
| `LEVH_SAFETY_BACKUP_DIR` | DB sibling | pre-replace online SQLite safety copies |

---

## 9. Testing & benchmarking

```bash
EMBEDDER_MODE=hash python -m pytest -q      # 122 tests, no torch/network needed
python scripts/benchmark_recall.py          # recall hit@k / MRR harness
EMBEDDER_MODE=local python scripts/benchmark_recall.py   # real quality signal
```

Tests force the **hash** embedder for determinism and zero dependencies. The
hash embedder is a positional-char fallback: it's non-semantic and only exists
so the system runs with no model. **Never benchmark quality on hash** — use
`local` (sentence-transformers), `ollama`, or `openai`. The interference test
intentionally leans on the hash embedder's prefix behavior; if you ever replace
the hash embedder, revisit `tests/test_v2_features.py::test_interference_*`.

---

## 10. Operational notes & known limits

- **Scale:** the NumPy vector store is fine to ~50K vectors; beyond that, swap
  in an ANN store (§7). Every search is O(n) over dimension-matched vectors.
- **Concurrency:** one shared `aiosqlite` connection serializes writes; `recall`
  mutates cached objects in place. Single-writer local use is safe; a
  high-concurrency multi-writer deployment would want a connection pool +
  per-memory locking.
- **WebSocket fan-out is in-process** — fine for one server, not for a
  horizontally-scaled cluster (would need Redis pub/sub).
- **Auth:** the optional token is a single shared secret, not per-user auth /
  multi-tenancy. Cloud sync / team sharing is intentionally **not** built —
  local-first is the product's whole thesis; a sync layer would be an optional
  add-on server, kept out of core so the zero-ops single-file story survives.
