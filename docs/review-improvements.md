# Code-Review Improvements (v2.2.x)

This pass applied targeted correctness, security, and robustness fixes on top
of v2.2. All 117 tests pass (`EMBEDDER_MODE=hash python -m pytest -q`).

## Correctness

1. **Vector-store cache staleness (highest-impact bug).**
   `recall()` scores candidates from the *cached* `Memory` objects held by the
   in-memory `VectorStore`. Four mutators — `set_importance`, `set_pinned`,
   `reinforce_memory`, `memory_feedback` — previously wrote only to SQLite, so
   pinning/importance/stability changes were invisible to ranking until the
   process restarted (e.g. a pinned memory was still decayed on recall). All
   mutators now funnel through `MemoryEngine._refresh_memory_caches()`, which
   replaces the vector-store copy and syncs the short-term deque.
   Regression tests in `tests/test_review_fixes.py`.

2. **Read-only recall.** `recall(..., reinforce=False)` (also exposed as
   `reinforce` on `RecallRequest`) skips reinforcement so dashboard/search
   previews don't inflate a memory's frequency or reset its decay clock —
   only genuine AI recall strengthens memories.

3. **`memory_type` validation.** An invalid `memory_type` now raises a clear
   `ValueError` in `store()` and returns HTTP **422** instead of a 500.

4. **Concurrency-safe `initialize()`.** Guarded with an `asyncio.Lock` +
   double-check so two concurrent first requests can't run the load twice.

## Security

5. **CORS locked down.** Default origins are now localhost only (was `*`),
   configurable via `LEVH_CORS_ORIGINS`. A wildcard on a local service
   let any visited website read the whole memory store from the browser.

6. **Optional shared-secret gate.** Set `LEVH_TOKEN` to require an
   `X-LEVH-Token` header on every `/api/*` call (except `/api/health`)
   and on the WebSocket (`?token=`). Unset = zero-config local use.

7. **Connector errors no longer leak upstream detail.** Connector `fetch`
   failures are logged server-side and returned as a generic 502, so tokens /
   URLs embedded in upstream exceptions aren't echoed to the client.

## Robustness / performance

8. **Shared HTTP client in the embedder.** OpenAI/Ollama calls reuse one
   `httpx.AsyncClient` instead of opening a new TCP/TLS connection per embed;
   closed on engine shutdown.

9. **OpenAI embedding retries transient failures** (429/5xx/network) with
   exponential backoff before surfacing the error, so a momentary hiccup no
   longer turns every store/recall into a 500.

10. **Missing indexes added** — `source` and a composite `(pinned, created_at)`
    matching the default list/search ordering.

11. **`import_memories` reports skipped records** via the `imported` event
    (`{count, skipped}`) instead of silently swallowing malformed rows.

## Tests

12. **Smoke test now runs in CI.** `tests/test_api_smoke.py` was a standalone
    `__main__` script that `pytest` never collected; it's now a proper
    `pytest`-collected async test (plus an invalid-type 422 case).

---

# New capabilities (addressing the competitive-analysis gaps)

These close the biggest gaps identified versus Mem0 / Zep. All additive and
tested; 122 tests pass.

13. **Auto-capture — session summarization.** `summarize_session()` distills a
    session's memories into one durable summary memory, via an **LLM**
    (`OPENAI_API_KEY`, `SUMMARY_MODEL`) or a deterministic **extractive
    fallback** offline. Runs automatically on `end_session` when
    `AUTO_SUMMARIZE_SESSIONS=true`. Exposed as REST
    `POST /api/sessions/{id}/summarize` and MCP tool `summarize_session`.
    (`server/core/summarizer.py`.) This was the #1 gap: managed services
    auto-distill conversations; LEVH previously relied on explicit
    `store`.

14. **Related memories (graph-lite) + recall trace.** `get_related()` returns a
    memory's nearest neighbours by embedding similarity — a live
    knowledge-graph "see also" with no extra schema. REST
    `GET /api/memories/{id}/related` and MCP tool `related_memories`. Combined
    with the existing per-memory score-breakdown endpoint, this gives a "why
    this memory / what's connected" view.

15. **Recall-quality benchmark harness.** `scripts/benchmark_recall.py` builds a
    labelled corpus and reports hit@1 / hit@3 / hit@5 / MRR, so recall quality
    is measurable and regressions are catchable (the way Mem0/Zep market
    accuracy). Run with `EMBEDDER_MODE=local|openai` for a real signal.

16. **Developer/architecture guide.** `docs/ARCHITECTURE.md` — module map,
    request lifecycles, the memory math, the invariants to preserve when
    extending, config reference, and operational limits.

---

# Dashboard integration (professionalizing the UI)

The new backend capabilities are wired into the dashboard, not just exposed as
raw endpoints — verified end-to-end in a real browser (Playwright), not just
by type-checking:

17. **Related Memories card** in the memory detail drawer (`memory-detail-
    drawer.tsx`) — shows nearest neighbours with similarity %, click one to
    jump the drawer to it (`onSelectRelated`, wired in both `app/page.tsx` and
    `app/memories/page.tsx`). Confirmed live: clicking a related item swaps
    the drawer's content, pin state, and forgetting curve to the new memory.
18. **Session summarization** — a "Summarize" button on each session card
    (`app/sessions/page.tsx`) calls `POST /api/sessions/{id}/summarize` and
    shows the resulting memory ID inline. Confirmed live: produced a new
    "Session summary (2 memories): …" memory visible in the Memories list.
19. **Recall Quality panel** in Settings (`app/settings/page.tsx`) — a
    "Run benchmark" button calls the new `POST /api/benchmark/recall`
    endpoint (added to `api.py`, reuses `scripts/benchmark_recall.py`) and
    renders hit@1/hit@3/hit@5/MRR as stat tiles, with a caveat note when the
    configured embedder is `hash` (non-semantic). Confirmed live: 50/70/80%
    hit@1/3/5, 61% MRR against the hash embedder.
20. **Auto-summarize status** surfaced as a badge in Server Configuration.
21. **Dashboard search no longer inflates memories.** `api.recallMemories`
    now defaults to `reinforce=false` — browsing/searching from the dashboard
    is read-only; only genuine AI-tool recall (MCP `recall_memory`, default
    `reinforce=true`) strengthens a memory. This ties the earlier backend fix
    (#2 in the correctness section above) directly into real UI behavior.

Frontend build verified clean (`npm run build` — lint + typecheck + static
export, 0 errors) and manually driven in a headless browser against a live
backend with seeded data; screenshots confirmed all three new panels render
and function correctly with no console errors.

## Deliberately deferred (with rationale)

- **Cloud sync / team sharing** — kept out of core on purpose. Local-first,
  single-file, zero-ops is the product thesis; sync belongs in an optional
  add-on server, not the engine.
- **Replacing the hash embedder** — it's a non-semantic offline fallback and
  the interference test leans on its prefix behavior. Real deployments use
  `local`/`ollama`/`openai`; improving the fallback has no production upside and
  would couple to test internals. Documented instead.
- **ANN vector store (Qdrant/pgvector)** — not needed under ~50K memories; the
  `VectorStore` interface is intentionally swap-ready when it is (see
  ARCHITECTURE §7).
