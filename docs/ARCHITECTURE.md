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
├── api.py               FastAPI app: builds it, installs middleware, includes routers,
│                        mounts the MCP SSE app and the static dashboard
├── middleware.py        The token gate and the public-demo boundary
├── routes/              One module per URL prefix; deps.py holds what they share
│   ├── memories.py      /api/memories collection routes (literal paths)
│   ├── memory_item.py   /api/memories/{id} routes — included after the collection,
│   │                    or the path parameter swallows /fading, /review, /low-trust
│   ├── guard.py         Mistake-guard rules and incidents
│   └── …                sessions, knowledge, context, system, data_transfer,
│                        connectors, entities, conflicts, onboarding, live
│
├── mcp_stdio.py         MCP server over stdio (Claude Desktop, Cursor, …)
├── mcp_sse.py           MCP server over SSE (mounted at /api/mcp/sse)
│
├── cli.py               `levh` dispatch
├── cli_parsers.py       build_parser(): every argparse definition
├── commands/            One module per command area; paths.py holds shared constants
├── entrypoint.py        Console script — the one place the version is derived
├── scaffold.py          `levh mcp init` project generator
├── configs.py           MCP client config generation (JSON / TOML / YAML per client)
│
├── core/
│   ├── engine_provider.py   Process-wide singleton engine (shared by ALL transports)
│   ├── memory_engine.py     The class and its construction; behaviour lives in engine/
│   ├── engine/              MemoryEngine mixins by responsibility — write, recall,
│   │                        decay, continuity, sessions, briefing, transfer, ingest,
│   │                        privacy, graph, dedupe, … plus helpers.py
│   ├── database.py          Connection, migrations, runtime status
│   ├── db/                  Query groups by table + schema.py (the DDL itself)
│   ├── types.py             Pydantic models (Memory, Session, RecallRequest, …)
│   ├── episodic.py          Thin Memory<->DB mapping layer
│   ├── short_term.py        Bounded FIFO deque (live working set)
│   ├── vector_store.py      NumPy cosine-similarity search (mixed-dimension safe)
│   ├── embedder.py          openai / local / ollama / hash, with graceful fallback
│   ├── hscore.py            H(x,ψ) math: scoring, decay, reinforce, weaken, curves
│   ├── admission.py         The gate: dedupe + secret redaction before storage
│   ├── guard.py             Mistakes → pinned rules + the violations log
│   ├── summarizer.py        Session auto-capture (LLM or extractive fallback)
│   └── librarian.py         Watchdog: which agents on this machine are wired to
│                            levh, who has gone quiet, and a chat that can act on it
│
├── tools/               One file per MCP tool; register.py wires them up and
│                        profiles.py decides which ones a client is shown
└── connectors/          Import sources: calendar / email / transcript /
                         local_files / obsidian / notion / github
```

The engine and the database are split into mixins rather than services because
their methods use the object's own state throughout; the split moved bodies
unchanged, so the public surface is provably identical (84 engine methods and
66 database methods before and after).

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
6. **Cross-instance cache coherence.** Invariant #1 covers this engine's own
   mutations; it says nothing about a *peer* engine (another process, or
   another `MemoryEngine` sharing this SQLite file) writing without this
   process knowing. `recall()` calls `_sync_with_external_writes()` first,
   which checks SQLite's own `PRAGMA data_version` (see
   `Database.data_version`) and does a full cache reload from `episodic` on a
   miss — cheap when nothing changed (one pragma query), a peer's write is
   the only thing that ever triggers the reload. A connection's own commits
   never change its own view of `data_version`, so this cannot loop on itself.
   Any new read path that scores from `vector_store`/`short_term` directly
   (as opposed to `episodic`, which always reads live) needs the same call at
   its top, or it inherits the staleness `recall()` no longer has.

---

## 7. Extension points

**Add an MCP tool:** create `server/tools/<name>.py` exposing
`register(mcp, engine)` with an `@mcp.tool()` coroutine, then add two lines to
`server/tools/register.py`, **and** give it a tier in `server/tools/profiles.py`
— a test locks the tier map to the registry, and the default profile is `work`,
so a tool outside it is invisible to most clients. (See `tools/related.py` / `tools/summarize.py` for
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
- **Librarian:** the watchdog scans in a worker thread but the engine's SQLite
  connection belongs to the server's event loop, so its writes are handed back
  with `run_coroutine_threadsafe` (`librarian.set_owner_loop`). Its chat can run
  a shell command the model proposes; the destructive-command filter is a
  blocklist — known patterns only — so treat `LEVH_LIBRARIAN_SHELL=0` as the
  real off switch on any machine where that is not acceptable.
- **Auth:** the optional token is a single shared secret, not per-user auth /
  multi-tenancy. Cloud sync / team sharing is intentionally **not** built —
  local-first is the product's whole thesis; a sync layer would be an optional
  add-on server, kept out of core so the zero-ops single-file story survives.
