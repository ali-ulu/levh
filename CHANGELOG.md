# Changelog

## 2.27.2

### Human and agent memory positioning

- Updated package, API, CLI, README, landing-page, and launch metadata to
  describe LEVH as a local-first memory layer for AI agents and humans.
- Added `levh --version` for standard CLI release identification.

## 2.27.1

### License metadata alignment

- Published the project metadata under GNU Affero General Public License v3.0
  or later (AGPL-3.0-or-later), matching the repository license.

## 2.27.0

### Full rename to LEVH

- Added the `levh` CLI as the primary command; `stackmemory` remains a
  deprecated compatibility alias with a warning.
- Added `LEVH_*` environment variables with `STACKMEMORY_*` fallback support.
- Updated generated MCP configs, dashboard text, README, and documentation to
  use LEVH-facing names.

## 2.26.8

### Release consistency and operational hardening

- Enforced local-first automatic embedding selection; cloud embeddings now
  require explicit opt-in.
- Added SQLite durability, migration, FTS, timeout, rate-limit, backup, and
  connector timeout hardening.
- Added release version consistency checks across the server, frontend, and
  packaged dashboard.

## 2.26.7

### Premium dashboard themes

- Added two production themes: **Aurora Glass** and **Deep Space**.
- Rebuilt the dashboard around live StackMemory data: system pulse, memory metrics, knowledge constellation, briefing, conflicts, review queue, people, and activity.
- Upgraded the global shell, navigation, search, quick capture, cards, inputs, and responsive behavior.
- Normalized six environment-specific npm tarball URLs to the official `registry.npmjs.org` registry so clean Docker builds are portable.
- Preserved all existing local-first, MCP, REST, onboarding, review, and memory workflows.

## 2.26.6

Operational Privacy & Durability hardening — closes the post-RC review gaps
without adding a memory feature.

- `EMBEDDER_MODE=auto` is now strictly local-first. An ambient
  `OPENAI_API_KEY` can no longer silently select remote embeddings; OpenAI
  embedding requires explicit `EMBEDDER_MODE=openai`.
- File-backed SQLite stores now use WAL mode, a configurable busy timeout,
  foreign keys and `synchronous=NORMAL`. Doctor reports the live journal,
  timeout, numbered schema version and FTS state.
- Added monotonic `PRAGMA user_version` migrations and an FTS5 index with
  insert/update/delete synchronization. Text filtering uses FTS relevance and
  safely falls back to `LIKE` on SQLite builds without FTS5.
- The optional token gate now uses constant-time comparison plus dependency-free
  per-client authentication/API rate limits. These limits are process-local and
  do not replace a reverse proxy for distributed deployments.
- Destructive replace restore creates a permission-restricted online SQLite
  safety backup before changing an existing file database; backup failure blocks
  the replace operation.
- GitHub and Notion connector clients now carry explicit connect/pool/read/write
  timeout defaults, preventing an omitted per-request timeout from hanging the
  service indefinitely.
- Docker's existing non-root user, health check and loopback-only Compose
  publication are now regression-tested alongside the new operational controls.

## 2.26.5

Black-Box Release Candidate Validation — closes the fifth comprehensive-audit
loop and adds no product feature.

- Added real MCP protocol subprocess tests for stdio and SSE: initialize, exact
  minimal-profile tool discovery, store, recall and clean shutdown are exercised
  over transport boundaries rather than direct function calls.
- Golden evaluation fixtures now ship inside the wheel, so `stackmemory eval run`
  works from an installed package with no source checkout or hidden tests path.
- Release validation covers clean frontend export, source and installed-wheel CLI,
  API health, MCP stdio/SSE, deterministic evaluation, deletion/privacy matrix,
  wheel metadata and forbidden-artifact checks.
- Docker runtime smoke remains environment-dependent; the default image/compose
  contract is tested statically and Docker unavailability is reported rather than
  misrepresented as a pass.

## 2.26.4

Connector & Runtime Robustness hardening — closes the fourth comprehensive-audit
loop without adding a connector or memory algorithm.

- Local-file connector rejects non-progressing chunk configurations, enforces
  `chunk_size > 0` and `0 <= overlap < chunk_size`, guarantees cursor progress,
  and refuses symlinked files that resolve outside the configured root.
- Legacy naive timestamps are normalized to UTC in trust recency scoring instead
  of crashing aware/naive datetime subtraction.
- Every newly embedded or re-embedded memory records provider, model, dimension,
  requested mode and provenance schema version. Gated JSON import discards
  untrusted vectors and replaces their provenance with the active local receipt.
- Doctor now detects mixed stored embedding dimensions and warns that incompatible
  vectors can be omitted from recall until re-embedded; homogeneous stores report
  the active dimension cleanly.
- Added robustness tests for chunk invariants, symlink escape, near-max overlap,
  naive timestamps, embedding receipts and gated import re-embedding.

## 2.26.3

Delete, Restore & Derived-State Integrity hardening — closes the third
comprehensive-audit loop without adding product features.

- Hard-delete now removes the primary memory plus entity links, persisted trust
  scores and every conflict candidate in one SQLite transaction; deletion audit
  proves absence across runtime, primary and derived layers and prunes orphan
  entities. Session memory counts are refreshed after deletion.
- Backup restore is now two-phase and fail-closed: every memory/session record and
  duplicate identity is validated before a replace can clear current data, the
  merge/replace is one transaction, and failures roll back with existing data
  intact. Caches, entity graph, conflicts, trust and session counts are rebuilt
  from committed SQLite state after restore.
- Mutations mark materialized entity/trust/conflict views dirty; the first derived
  read performs one deterministic reconciliation. Content edits remove obsolete
  entity links and stale open conflict candidates, while pin/review/feedback
  changes invalidate cached trust breakdowns.
- Consolidation lineage now stores source IDs, timestamps and SHA-256 receipts
  instead of a second undeletable copy of source content. Summaries derived only
  from demo memories remain demo-tagged for safe cleanup.
- Added integrity tests for complete purge, non-destructive malformed restore,
  post-restore rebuild, stale-conflict pruning, entity reconciliation, trust
  invalidation and session-count correctness.

## 2.26.2

Admission & Privacy Integrity hardening — all default user-facing write paths
now pass through the deterministic admission gate before persistence.

- REST, WebSocket, MCP `store_memory`, CLI `capture`, legacy connector imports,
  and JSON imports now redact secrets and reject/review low-quality or duplicate
  content before it reaches SQLite. Explicit REST `force=true` remains an
  audited override; WebSocket and MCP do not expose an implicit bypass.
- Stored admission receipts now include machine-readable reason codes, duplicate
  similarity, redaction evidence, and override status. Secret-audit previews are
  redacted and never echo the detected secret.
- User JSON imports preserve portable lifecycle metadata but discard untrusted
  embeddings, recompute vectors locally, persist to SQLite before updating
  process caches, and return an explicit imported/redacted/duplicate/held/error
  breakdown. Backup restore retains its separate trusted-state path pending the
  transactional restore hardening loop.
- Non-loopback `serve` binds now require `STACKMEMORY_TOKEN`; Docker publishes
  only on `127.0.0.1` by default and runs as a non-root user.
- Added adversarial ingress tests covering REST, MCP, CLI, JSON import, secret
  preview leakage, non-loopback binding, container defaults, and ghost-cache
  prevention.

## 2.26.1

Config & Clean Release hardening — closes the first comprehensive-audit loop
without adding product features.

- Added one canonical runtime resolver with the precedence `explicit override →
  environment → .stackmemory/config.json → defaults`. CLI, API, MCP, doctor,
  onboarding, generated client configs, and default `MemoryEngine()` creation
  now resolve the same database/embedder/runtime settings.
- Custom database paths created by `stackmemory init` / `stackmemory setup` are
  used by later `capture`, `serve`, doctor, API and MCP processes instead of
  silently falling back to `./stackmemory.db`. Relative paths are resolved from
  the working directory; malformed present config fails clearly.
- Replaced the React-19-incompatible `lucide-react` / `next-themes` versions
  with compatible releases and removed every `--legacy-peer-deps` bypass.
- Release, CI and Docker now use the same clean `npm ci` lockfile contract; the
  release pipeline always discards warmed `node_modules`, applies a bounded
  build timeout, disables Next telemetry, and asserts `out/index.html`.
- Added Node/npm support metadata (`.nvmrc`, `engines`, `packageManager`) and
  regression tests for runtime-config precedence, cross-process DB-path
  consistency, MCP config consistency and frontend peer compatibility.

## 2.26.0

First-Run Onboarding & Product Polish — no new memory algorithm, connector,
LLM adapter, cloud service, or MCP tool. This release turns the existing local
product into a clear demo-or-real setup path.

- **`stackmemory setup`**: deterministic first-run command with `--demo`,
  `--real`, `--status`, `--client`, and `--profile`. It initializes storage
  without destructive reset, generates one focused MCP config under
  `.stackmemory/mcp/`, prints exact next commands, and writes a privacy-safe
  local onboarding receipt containing configuration/status metadata only.
- **Computed onboarding readiness**: `GET /api/onboarding/status` derives first
  run, memory count, demo state, MCP configuration state, profile counts,
  dogfood state, and the recommended next step from real local state — not a
  manually maintained UI flag.
- **Two-path dashboard onboarding**: Try deterministic demo data or store and
  recall a real first memory through the existing pipeline. The card can
  generate client/profile MCP configs, explains that profiles are not a
  security boundary, and shows local/off-by-default dogfood behavior.
- **Demo-data provenance and cleanup**: demo memories remain marked
  `metadata.demo=true`, carry a visible badge, and can be removed through an
  explicit confirmed action that purges only demo-tagged memories, preserves
  real memories, verifies deletion residue, and rebuilds derived graph/trust
  state.
- **Doctor polish**: database writability, packaged dashboard, MCP registry,
  dogfood state and journal discovery, memory count, and onboarding
  recommendation are now reported as PASS/WARN/FAIL. An empty database is a
  first-run WARN, not a failure; dogfood being off is informational.
- **Privacy constraints**: no remote telemetry, no raw memory/query content in
  the receipt, no sensitive absolute paths in onboarding API output, and no
  network requirement.

## 2.25.2

Dogfood Journal Path Consistency — the live engine provider and
`dogfood status`/`export` now use one resolver with the precedence
`--journal → DOGFOOD_JOURNAL_PATH → SQLITE_DB_PATH sibling → cwd`. This closes
the case where a service wrote beside the database while the CLI reported an
empty journal from the current directory.

## 2.25.1

Dogfood Runtime Wiring — the 2.25 independent audit found the dogfood journal
existed but nothing attached it on the live path. This narrow patch closes
that gap; no new product surface.

- **Opt-in live instrumentation**: `STACKMEMORY_DOGFOOD_ENABLED` (default
  **off**). When set, the shared engine provider (`server/core/engine_provider.py`)
  attaches the dogfood journal to the process-wide engine, so the REST API /
  `serve`, MCP stdio, and MCP SSE transports all journal automatically. The
  journal defaults to `dogfood_events.jsonl` next to the SQLite database;
  `DOGFOOD_JOURNAL_PATH` overrides.
- **Product-surface events**: the engine now emits `briefed`,
  `meeting_prepped`, and `trust_viewed`, and the journal listener maps
  those plus the existing `reviewed` / `demo_seeded` events to
  `briefing_opened`, `meeting_prep_opened`, `trust_viewed`,
  `review_keep/reinforce/weaken/forget`, and `seed_demo_completed` — so
  time-to-first-briefing / meeting-prep and the review distribution fill in
  from real usage, not just manual `record()` calls.
- **Double-attach guard**: `DogfoodJournal.attach()` is idempotent per engine
  (second attach is a no-op) — events can't be double-journaled.
- **Honest `duplicate_rate`**: the admission gate now returns machine
  `reason_codes` (`too_short`, `duplicate_exact`, `duplicate_near`,
  `secrets_redacted`, `admitted`) and the evaluator counts duplicates by
  those codes instead of assuming every reject/review is a duplicate.
- **Metric scope named**: `low_trust_count` → `checked_low_trust_count`
  (only memories a fixture checks via `expected_trust_labels`).
- Unchanged rules: no network, no cloud telemetry, no raw memory content in
  the journal or any export. New tests: `tests/test_dogfood_wiring.py`.

## 2.25.0

Memory Evaluation & Dogfood Gate — a way to measure the memory system against
itself before deciding whether it's actually helping, plus a local usage
journal so that judgment can be based on real signals instead of vibes.

- **Golden-fixture evaluation** (`server/core/evaluation.py`): runs 9 fixture
  scenarios (`tests/fixtures/evaluation/*.json`) through the real pipeline —
  admission gate → store → trust recompute → conflict detection → recall →
  review — entirely offline, no LLM, no mocks. Metrics: recall hit@1/hit@3/MRR
  plus forbidden-supersession violations (a superseded fact outranking its
  replacement), admission accept/review/reject/redaction/duplicate rates,
  conflict-candidate precision/recall/false-positive count (one fixture,
  `09_conflict_false_positive_guard.json`, deliberately measures a known false
  positive rather than hiding it), lifecycle review distribution + fading
  recovery rate, and seed-demo completion. Deterministic run-to-run for a
  given fixture set + embedder mode (hash embedder, fixture keys stand in for
  generated ids — no timestamps or uuids in the report). The report contains
  fixture keys, scenario names, labels, and numbers only — never the raw
  memory content that was stored.
- **`stackmemory eval run [--fixtures --embedder-mode --output]`** runs the
  evaluation and writes a JSON report (default `eval_report.json`); **`stackmemory
  eval report`** prints the last written one.
- **Local dogfood journal** (`server/core/dogfood.py`): an append-only JSONL
  file (`dogfood_events.jsonl`, path overridable via `DOGFOOD_JOURNAL_PATH`) of
  coarse usage events from a whitelisted event-type and attribute set — no
  content, no query text, ever, rejected at the API boundary. No network I/O,
  no default telemetry: nothing is recorded unless the running install
  attaches the journal, and nothing is sent anywhere. **`stackmemory dogfood
  status`** prints the aggregate view (event counts, time-to-first-value,
  recall-feedback rate, review distribution). **`stackmemory dogfood export
  --output report.json`** is an explicit user action that writes only the
  aggregate report — raw event lines never leave the journal file on their
  own.
- **No fabricated benchmark numbers**: this changelog and the README
  deliberately carry no specific metric values (no "hit@1 = 0.86" style
  claims). Every number in an evaluation report is tied to the fixture set
  version that actually produced it (`evaluation_version: memory-eval-v1`,
  recorded alongside the installed `stackmemory` version) — quote numbers only
  from a real `stackmemory eval run`, never from memory.
- **MCP tool profiles are not a security boundary**: `minimal`/`work` exclude
  destructive admin tools (`restore_backup`, `purge_memory`, `redact_secrets`,
  `forget_memory`, and other `admin`-tier tools) from what's *advertised* to a
  client, but that's tool-discovery surface reduction for selection accuracy —
  not access control. A client on any profile still talks to the same engine
  instance; profiles don't authenticate or authorize anything.

## 2.24.0

Demo Reliability & MCP Surface Gate — harden the first-run demo, the conflict/
trust flow, and the MCP tool surface before adding any LLM contradiction adapter
(deliberately deferred: current research favors deterministic, retrieval-aware
conflict handling over LLM judgment, so the offline core stays intact).

- **MCP tool profiles**: advertising all 59 tools to a client hurts tool-selection
  accuracy, so tools are grouped into cumulative profiles —
  `minimal` (5) ⊂ `work` (15) ⊂ `admin` (54) ⊂ `full` (59). Selected via
  `STACKMEMORY_MCP_PROFILE`; a register-time filter advertises exactly the
  profile's tools without touching any of the 39 tool modules. New
  `server/tools/profiles.py`. The **server's unset default stays `full`** (no
  surprise tool loss on upgrade), while **generated configs default to `work`**.
- **`stackmemory mcp config --profile <name>`** and a new **`stackmemory mcp
  profiles`** command that lists the bands and their tool counts. Generated
  configs now carry `STACKMEMORY_MCP_PROFILE`.
- **5-minute demo doc** (`docs/demo/5-minute-demo.md`): install → `seed-demo` →
  `serve` → tour of briefing / entities / trust / the one conflict / insights →
  MCP recall, all offline.
- **Conflict-eval fixture** (`tests/test_demo_conflict_eval.py`): locks the
  seeded demo to exactly one meaningful conflict candidate (the Atlas deadline),
  no spurious "use/use" collision, a shared person entity, trust context
  present, and — critically — a *candidate* not a verdict (never auto-resolved,
  confidence < 1.0). Plus deterministic seed-count locks in `test_seed_demo.py`
  and full profile coverage in `tests/test_mcp_profiles.py`.
- **README alignment**: the quickstart and the dashboard empty state now describe
  the same `install → init → seed-demo → serve` path; MCP-tools section documents
  profiles.
- **Release artifact hygiene**: the release zip now also excludes `*.tsbuildinfo`,
  `*.db`/`-wal`/`-shm`, `*.log`, `.env`, and `logs/` (keeps `.env.example`), and
  the pipeline fails if any forbidden artifact slips into the zip.

No LLM adapter, no new connectors, no network requirement; MCP tool count
unchanged at 59.

## 2.23.1

Onboarding — a first run no longer stares back with empty states. Ships a
deterministic demo corpus and a "try it in 5 minutes" path.

- **`stackmemory seed-demo`**: loads a small, self-consistent slice of a
  fictional engineer's work life (4 people, 2 organizations, 2 projects, 3
  decisions, 3 tasks, meetings, and **one genuine conflict candidate** — a
  disputed Atlas deadline) into an empty store. Every memory flows through the
  real store path and is backdated from its age, so the forgetting curve,
  review queue, briefing, trust breakdown, entity graph, and conflict review
  all light up immediately. Refuses to run on a non-empty store unless
  `--force`.
- **`POST /api/seed-demo`** and `api.seedDemo()` expose the same seed to the
  dashboard.
- **Empty-state onboarding** on the dashboard home: when the store is empty, a
  "Load demo data" button seeds the corpus in place (no reload) plus a
  "try it in 5 minutes" quickstart pointing at Briefing, Meeting Prep, and
  Conflicts.
- New `server/core/demo_data.py` holds the corpus as pure, declarative data;
  `MemoryEngine.seed_demo()` orchestrates ingest → entity reindex → trust
  recompute → conflict detection. Covered by `tests/test_seed_demo.py`.

## 2.23.0

Authenticated dashboard + a deterministic, one-command release pipeline. The
backend token gate (added earlier) now has a first-class dashboard story, and
the release drift that produced 2.22.1's recovery can no longer happen silently.

- **Token-aware dashboard**: when the server is started with `STACKMEMORY_TOKEN`,
  the dashboard now works end-to-end. Every `/api/*` call carries the
  `X-StackMemory-Token` header and the `/ws/memory` WebSocket appends `?token=`
  (browsers can't set WS headers), both sourced from a small SSR-safe token store
  (`frontend/src/lib/token.ts`, localStorage).
- **Auth gate**: a new `AuthGate` wraps the routed content. It reads
  `GET /api/health.auth_required` (health is never gated) and, only when the
  server requires a token and none is stored, shows a token-entry card. A health
  hiccup never blocks the app.
- **Settings → Server access token**: view whether a token is stored, save a new
  one, or clear it — stored locally in the browser, needed only when the server
  sets `STACKMEMORY_TOKEN`.
- **`/api/health` reports `auth_required`** so the frontend can detect up-front
  whether a token is required.
- **Deterministic release pipeline** (`scripts/release.py`): one command runs
  bump → clean `next build` → sync into `server/dashboard` → **version-consistency
  assertion** → wheel + `twine check` → source zip, in that order. Building
  *after* the bump makes the 2.22.0 "packaged dashboard lags source" drift
  structurally impossible; `--check` verifies consistency on its own. Covered by
  `tests/test_release_pipeline.py`.
- **next.config fix**: removed the invalid top-level `outputFileTracing` key that
  Next 15.5 warned about (static export never runs file tracing).

## 2.22.1

Recovery release — fixes three Trust-card UI defects found in an external audit of
2.22.0, and closes the release-pipeline gap that shipped a stale dashboard build.

- **Trust card refreshes after actions**: pin/unpin, reinforce, and mark-stale now
  re-fetch the trust breakdown, so the displayed confidence can't go stale mid-session.
- **Stale-response guard**: switching memories while a trust fetch is in flight can no
  longer show memory A's trust under memory B (request-identity check on every update).
- **Error state is reachable**: a failed trust fetch now shows "Could not load trust
  score." instead of silently hiding the whole card.
- **Release pipeline fix**: 2.22.0's packaged dashboard still displayed v2.21 because
  the frontend build ran *before* the version bump. Order corrected (bump → build →
  sync → assert), and new packaging tests (`tests/test_packaging.py`) now assert that
  pyproject, `server/api.py`, the sidebar source, and every file in the **packaged**
  dashboard agree on the version — so this class of drift fails the suite instead of
  shipping.

## 2.22.0

Trust & Graph UI deepening — surfaces the signals the last few releases built, in the
dashboard. Frontend-only; no API, engine, or tool changes (still 59 MCP tools).

- **Memory detail drawer** now shows a **Trust** card: a colored confidence pill
  (label-tinted), a bar per component (source / corroboration / review / recency, minus a
  risk bar), the top explanation lines, and an amber warning row when the memory has an
  open or confirmed **conflict candidate**.
- **Graph page** gains an inline-SVG **relationship mini-map** in the entity detail view:
  the selected entity at the centre with its co-occurring entities as satellites, edge
  weight scaled by how many memories they share, nodes colored by entity type; clicking a
  satellite navigates to it.
- **Insights page** gains a **Trust distribution** card: a "Recompute trust" button, a
  by-label bar chart (high → very_low, consistent colors), and a "lowest-trust memories"
  list.
- New shared `frontend/src/lib/trust-ui.ts` (label → color helpers) keeps trust colors
  consistent across the drawer, graph, and insights.

## 2.21.0

Conflict Candidates — deterministic, offline flagging of memories that MIGHT disagree,
for human review. **Not** LLM contradiction detection, **not** a truth engine, **never**
auto-deletes: it only surfaces candidates and lets a human decide. Signal, not verdict.

- **Core** (`server/core/conflict.py`): `opposing_signal(a, b)` detects an opposing
  surface pattern between two texts — an **antonym** ("approved" vs "rejected", "main" vs
  "prod"), a **negation** ("is X" vs "is not X"), or an **attribute_value** clash (same
  key, different value: "use npm" vs "use pnpm", "budget is 30k" vs "50k", "meeting at
  10:00" vs "14:00"). Curated antonym list + narrow regex — no broad NLP, no LLM.
- **Engine**: `detect_conflict_candidates()` pairs memories that **share an entity** (via
  the entity graph) AND show an opposing signal, storing each as a candidate (idempotent —
  never resets an already-reviewed one). `list_conflict_candidates(status)` and
  `review_conflict_candidate(id, action)` with actions dismiss / confirm / resolve_keep_a /
  resolve_keep_b / mark_both_valid / human_review. No memory is ever auto-deleted;
  resolve_keep_* only weakens the not-kept memory as an explicit user choice.
- **Trust integration**: an open conflict adds a small **risk** signal to the trust score
  (+0.15), a confirmed one more (+0.25), a dismissed one none — a candidate lowers
  confidence pending review, it never proves a memory wrong. Both memories' trust scores
  are cited in the candidate's explanation.
- **Storage**: new `memory_conflict_candidates` table (status: open/dismissed/confirmed/resolved).
- **REST**: `POST /api/conflicts/detect`, `GET /api/conflicts?status=`,
  `POST /api/conflicts/{id}/review`.
- **MCP tools**: `detect_conflict_candidates`, `list_conflict_candidates`,
  `review_conflict_candidate`. **59 MCP tools** total (+3).
- **CLI**: `stackmemory conflicts detect | list | review <id> --action …`.
- **Dashboard**: new **Conflicts** page — each candidate with both memory previews, the
  signal, shared entities, and dismiss/confirm/keep-A/keep-B/both-valid actions.
- 17 new tests (`tests/test_conflict_candidates.py`), including one asserting recall
  ordering is unchanged and that the trust score is never renamed to a "truth" score.

## 2.20.0

Provenance / Trust Score — a deterministic, explainable *reliability* signal for each
memory, **separate from the H(x,ψ) recall score** and never affecting recall ranking.
This is provenance, not truth: it summarises where a memory came from and how well it's
corroborated — it does not claim the memory is factually correct. No LLM, no network.

- **Core** (`server/core/trust.py`): `confidence = 0.30·source + 0.25·corroboration +
  0.20·review + 0.15·recency − 0.10·risk`, clamped to [0,1], with a label
  (high / medium_high / medium / low / very_low):
  - **source** — base reliability by source type (human/pinned 0.85 → email 0.75 →
    unknown raw 0.45);
  - **corroboration** — how many **distinct source types** reference the same entities
    (via the entity graph); repeated memories from the *same* source can't inflate it;
  - **review** — pinning / reinforcement / positive review raise it, weakening lowers it;
  - **recency** — freshness by `created_at` (kept independent of the H-score's decay);
  - **risk** — redaction, rejected/held admission, weakening, unknown provenance.
- **Engine**: `recompute_trust_scores()` (scores + persists all), `get_trust(id)`
  (computes/caches on demand, returns an explainable breakdown with evidence +
  human-readable explanation), `list_low_trust(threshold)`.
- **Storage**: new `memory_trust_scores` table (queryable per-component + full breakdown).
- **REST**: `GET /api/memories/{id}/trust`, `POST /api/memories/trust/recompute`,
  `GET /api/memories/low-trust`.
- **MCP tools**: `memory_trust`, `recompute_trust_scores`, `list_low_trust_memories`.
  **56 MCP tools** total (+3).
- **CLI**: `stackmemory trust show <id> | recompute | low`.
- **Dashboard**: Settings → **Trust & provenance** card (recompute + label distribution +
  lowest-trust list).
- 13 new tests (`tests/test_provenance_trust.py`), including an assertion that recomputing
  trust leaves recall ordering unchanged.

## 2.19.0

Entity Knowledge Graph — persistent, queryable. Where People / Organizations / Decisions
were computed on the fly, memories are now indexed into real `entities` + `memory_entities`
tables, so "which memories mention X" and "which entities co-occur with X" run as a join
instead of a full re-scan. Deterministic, offline.

- **Extraction** (`server/core/entities.py`): `extract_entities(memory)` produces the typed
  entities a memory references — **person, organization, event, document, task** — reusing
  the people/org metadata extractors and content markers (calendar/transcript → event,
  notion/obsidian/local-files → document, action-item phrasing → task).
- **Graph store** (new `entities` + `memory_entities` SQLite tables): each entity keyed as
  `<type>:<key>`; a memory↔entity link table powers co-occurrence queries.
- **Engine**: `reindex_entities()` rebuilds the graph (idempotent); `list_entities_graph(type)`
  lists entities by mention count; `get_entity(query)` returns an entity's profile — the
  memories that mention it **and** its graph neighbours (co-occurring entities, ranked by
  shared memories); `entity_graph_stats()` counts by type.
- **REST**: `POST /api/entities/reindex`, `GET /api/entities/stats`, `GET /api/entities?type=`,
  `GET /api/entities/{id}`.
- **MCP tools**: `reindex_entities`, `list_entities`, `about_entity`. **53 MCP tools** total (+3).
- **CLI**: `stackmemory entities reindex | list [--type] | about <query>`.
- **Dashboard**: new **Graph** page — filter entities by type, click one to see its memories
  and related entities.
- 12 new tests (`tests/test_entity_graph.py`).

## 2.18.0

Hard-delete audit & redaction — the trust layer. Proves a `forget` really removed
everything, and lets you find and strip secrets that were stored before the admission
gate existed. Deterministic, offline.

- **Hard-delete audit**: `audit_deletion(id)` checks whether a memory still lingers in
  ANY layer (short-term deque, in-memory vector store, SQLite) — so a deletion is
  *provable*, not assumed. `purge_memory(id)` hard-deletes and returns the post-condition
  audit (`purged: true` only when every layer is clean).
- **Retroactive redaction**: `audit_secrets()` scans stored memories for credentials
  (reusing the admission gate's `redact_secrets`); `redact_memory(id)` strips secrets from
  a single memory in place (rewrites content, re-embeds, logs to `metadata.redaction_history`);
  `redact_all_secrets(dry_run=True)` previews or applies a bulk sweep.
- **Idempotency fix (core)**: `redact_secrets` now skips an already-redacted `key=[REDACTED]`
  value, so redacting the same content twice is a true no-op for assignment-shaped secrets
  (not just standalone tokens).
- **REST**: `GET /api/memories/audit-secrets`, `POST /api/memories/redact-all`,
  `POST /api/memories/{id}/redact`, `POST /api/memories/{id}/purge`.
- **MCP tools**: `audit_secrets`, `redact_secrets`, `purge_memory`. **50 MCP tools** total (+3).
- **CLI**: `stackmemory audit-secrets`, `stackmemory redact-secrets [--apply]`,
  `stackmemory purge <id>`.
- **Dashboard**: Settings → **Privacy & Redaction** card (scan for secrets, redact all).
- 15 new tests (`tests/test_redaction_audit.py`).

## 2.17.0

Connector Framework v2 — gate-integrated, incremental ingest. Adding capture sources is
now safe: every fetched item is routed through the Admission Gate (2.16) instead of blind
storage, so re-syncing a source doesn't pile up duplicates or leak secrets.

- **Engine**: `ingest_items(items, connector, project=None, use_gate=True)` stores a batch
  with per-item **error isolation** (one bad item can't fail the run), routes each through
  the admission gate (dedupe + secret redaction) when `use_gate`, and records the run in a
  new `connector_sync` table. Returns a breakdown: `fetched, stored, redacted, duplicates,
  held, errors`. `list_sync_state()` reports per-source bookkeeping (last-synced, totals,
  run count) so re-syncing is **incremental and reportable** ("0 new since last sync").
- **Admission core fix**: the duplicate probe now embeds the *redacted* text (what would
  actually be stored), so re-ingesting the same secret-bearing item is correctly detected
  as a duplicate instead of accumulating near-identical copies on every sync. (This also
  closes the edge case noted in 2.16.)
- **DB**: new `connector_sync` table + `record_sync` / `get_sync_state` / `list_sync_states`
  (created automatically; existing databases upgrade transparently).
- **REST**: `POST /api/connectors/sync` (v2, gate-filtered) and `GET /api/connectors/sync-state`.
  The original `POST /api/connectors/import` is kept for back-compat.
- **MCP tools**: `sync_connector`, `connector_sync_status`. **47 MCP tools** total (+2).
- **CLI**: `stackmemory sync <connector> [--config K=V …] [--no-gate]` and `stackmemory sync --status`.
- **Dashboard**: the Connectors import card gains a "Route through admission gate" toggle
  (shows stored / duplicates skipped / secrets redacted) plus a read-only Sync history list.
- 9 new tests (`tests/test_connector_sync.py`).

## 2.16.0

Memory Admission Gate — a quality gate that decides what happens to an incoming memory
*before* it is stored, so growing the number of capture sources doesn't grow the noise.
Deterministic and offline (no LLM). This is the prerequisite for expanding connectors:
first decide what gets in.

- **Core** (`server/core/admission.py`): `evaluate(content, max_similarity, …)` returns
  one of four verdicts, precedence reject > review > redact > admit:
  - **reject** — empty/too-short, or a near-exact duplicate (similarity ≥ 0.98);
  - **review** — a near-duplicate (≥ 0.90): held for a human, not auto-stored;
  - **redact** — secrets stripped before storing (`password=…`, AWS keys, private-key
    blocks, bearer / GitHub / Slack tokens → `[REDACTED]`). Normal emails are NOT
    secrets — they feed the people graph — so they're kept;
  - **admit** — stored as-is.
  `redact_secrets(content)` is a standalone, reusable helper.
- **Engine**: `evaluate_admission(content, project, min_length)` (preview, no store) and
  `admit_memory(…, force=False)` (applies the verdict; reject/review not stored unless
  forced; the verdict is recorded in `metadata.admission`).
- **REST**: `POST /api/memories/evaluate-admission` and `POST /api/memories/admit`.
- **MCP tools**: `evaluate_admission`, `admit_memory`. **45 MCP tools** total (+2).
- **CLI**: `stackmemory admit "<text>" [--project P] [--force]`.
- **Dashboard**: Settings → **Admission Gate (preview)** card — paste text, see the
  verdict, reasons, and redacted preview (read-only, never stores).
- 20 new tests (`tests/test_admission.py`).

## 2.15.0

Spaced-Repetition Review — roadmap Phase 5. Closes the memory lifecycle loop:
store → recall → decay → **review** → reinforce / weaken / forget. Turns the passive
fading queue into an active, human-in-the-loop review flow. No LLM.

- **Engine**: `review_queue(threshold=0.5, project=None, limit=20)` surfaces fading,
  unpinned, un-snoozed memories with the decay context needed to decide — retention,
  stability, last-accessed, recall count, and a human-readable `reason`.
  `apply_review(memory_id, action, snooze_days=7, reason="")` applies one of six actions:
  - **keep** — reset the decay clock (mild reinforcement), no stability change;
  - **reinforce** — strong stability boost (uses the existing reinforce path);
  - **weaken** — lower stability so it fades faster (existing negative-feedback path);
  - **pin** — never decays, never auto-forgotten;
  - **forget** — remove via the existing forget path;
  - **snooze** — push the next review out by `snooze_days`.
  Every decision is recorded auditably in `metadata.review` + `metadata.review_history`.
- **REST**: `GET /api/memories/review` (declared before `/{memory_id}` to avoid path
  shadowing) and `POST /api/memories/{memory_id}/review`.
- **MCP tools**: `list_review_memories`, `review_memory`. **43 MCP tools** total (+2).
- **CLI**: `stackmemory review list` and `stackmemory review apply <id> --action …`.
- **Dashboard**: new **Review** page — a card per fading memory with Keep / Reinforce /
  Weaken / Pin / Snooze / Forget buttons.
- 18 new tests (`tests/test_spaced_repetition.py`): queue selection, pinned & snooze
  exclusion, each action's decay effect, audit history, route-shadowing, API & MCP flows.

## 2.14.0

Memory Consolidation — roadmap Phase 5, deepening the human-memory model. Models how
sleep compresses many similar episodes into a durable gist: clusters of related older
memories collapse into one consolidated memory, and the raw episodes fade from active
recall.

- **Engine**: `consolidate_memories(similarity_threshold=0.82, min_age_days=7,
  min_cluster_size=2, project=None, dry_run=True)`. Unlike `dedupe` (removes
  near-identical duplicates at a high threshold, keeps one verbatim), consolidation uses
  a *lower* threshold to group **related** memories and replaces each cluster with an
  LLM/extractive summary. Safeguards: pinned and recent (< `min_age_days`) memories are
  never touched, and already-consolidated summaries aren't recompressed. The originals
  are preserved inside `metadata.consolidated_from`, so nothing is lost — they're
  archived, not deleted.
- **REST**: `POST /api/memories/consolidate-similar` (dry-run preview or apply).
- **MCP tool**: `consolidate_similar` — preview clusters, then apply. **41 MCP tools**
  total (+1). (Named distinctly from the existing short-term-promotion
  `consolidate_memories` tool.)
- **Dashboard**: Settings → Data Management — **Preview consolidation** / **Consolidate**
  buttons alongside dedupe.
- 10 new tests (`tests/test_consolidation.py`): clustering, dry-run safety, pinned &
  age exclusion, archive integrity, idempotence, project filter, API & MCP flows.

## 2.13.0

Meeting Prep — roadmap Phase 3, the proactive "before you walk in" brief. This is
where the entity layers pay off: People, Decisions, commitments, and the calendar
all converge into one pre-meeting view. Deterministic and offline.

- **Engine**: `meeting_prep(query="", within_days=14)` picks the next upcoming meeting
  (an event with attendees, or a calendar/transcript memory dated in the future) — or,
  with `query`, the best-matching one — then assembles:
  - the meeting (title, time, attendees, project);
  - per attendee, **what you last discussed with them** (their recent memories, newest
    first, with an interaction count);
  - **relevant open commitments** — action items in the same project, or that name an
    attendee (reuses the shared commitment detector);
  - **recent decisions** for the meeting's project.
- **REST**: `GET /api/meeting-prep?query=&within_days=`.
- **MCP tool**: `meeting_prep` — a readable MEETING / WHO YOU'RE MEETING / OPEN
  COMMITMENTS / RECENT DECISIONS brief. **40 MCP tools** total (+1).
- **Dashboard**: new **Meeting Prep** page — search a meeting or auto-load the next one,
  with attendee context, commitment, and decision cards.
- Refactor: extracted a module-level commitment pattern + `_first_marker_sentence`
  helper and an `_event_when` timestamp helper, shared by meeting-prep (briefing keeps
  its own tested copy).
- 12 new tests (`tests/test_meeting_prep.py`).

## 2.12.0

Encrypted backup & restore — roadmap Phase 0 (security & durability). A StackMemory
store can hold a person's entire work-life memory, so it must be portable and
protectable at rest.

- **Full snapshot**: `engine.backup()` captures every memory *with its complete decay
  state* (stability, recall counts, importance, pins, metadata) plus every session into
  one self-describing envelope. `engine.restore(snapshot, replace=False)` merges by
  default (same-id rows overwritten) or, with `replace=True`, wipes first so the store
  becomes an exact copy.
- **At-rest encryption** (`server/core/crypto.py`): optional passphrase encryption using
  the well-reviewed `cryptography` library — PBKDF2-HMAC-SHA256 (390k rounds) key
  derivation + Fernet (AES-128-CBC + HMAC). Authenticated, so a wrong passphrase or a
  tampered file fails loudly instead of returning garbage. Unencrypted backups still work
  if the dependency is ever absent.
- **REST**: `POST /api/backup` (returns a downloadable file; `passphrase` encrypts it) and
  `POST /api/restore` (base64 body, auto-detects encryption, `replace` flag).
- **MCP tools**: `create_backup(path, passphrase)` and `restore_backup(path, passphrase,
  replace)` — write/read a local backup file. **39 MCP tools** total (+2).
- **Dashboard**: Settings → **Backup & Restore** — download an (optionally encrypted)
  backup, and restore from a file with merge/replace choice.
- Adds `cryptography>=42` to core dependencies (also exposed as the `secure` extra). New
  DB helpers `clear_all_memories` / `clear_all_sessions` back replace-mode restore.
- 22 new tests (`tests/test_backup.py`): crypto round-trip, wrong-passphrase &
  tamper rejection, merge/replace restore, decay-state preservation, API & MCP flows.

## 2.11.0

Entity layer — roadmap Phase 2. Two new deterministic, offline entities layered over
captured memory:

- **Organizations** — the people graph rolled up one level by **email domain**.
  Reuses the same metadata extraction as People (`people.extract_people`), so there's
  one place that understands connector shapes. `domain_to_org` turns `mail.acme.co.uk`
  → "Acme"; free/personal providers (gmail, outlook, …) are excluded since they aren't
  organizations. Each org carries its people, sources, memory count, and last-seen.
  - **Engine**: `list_organizations(limit)`, `get_organization(query)`.
  - **REST**: `GET /api/organizations`, `GET /api/organizations/{key}` (404 when unknown).
  - **MCP tools**: `list_organizations`, `about_organization`.
  - **Dashboard**: **Organizations** page (list + per-org detail with people & memories).
- **Decisions** — deterministic detection of decision statements in episodic content
  ("we decided", "agreed to", "we chose", "going with", "karar verdik", "üzerinde
  anlaştık", …). The containing sentence is extracted, de-duplicated, windowed by days,
  most-recent-first — "what did we decide, and when/where". No LLM.
  - **Engine**: `list_decisions(project, days, limit)`.
  - **REST**: `GET /api/decisions?days=&project=&limit=`.
  - **MCP tool**: `list_decisions`.
  - **Dashboard**: **Decisions** page with a 30d/90d/1y range switch.
- Shared `_event_date()` helper now underpins `timeline()`, `briefing()`, and
  `list_decisions()` so "when did this happen" is answered identically everywhere.
- **37 MCP tools** total (+3). 27 new tests (`tests/test_organizations.py`,
  `tests/test_decisions.py`).

## 2.10.0

Daily Briefing — roadmap Phase 3 (the killer feature): memory stops being a passive
store and becomes an active assistant. Fully deterministic and offline — no LLM key
required, so it runs anywhere and its output is reproducible.

- **Engine**: `briefing(project=None, days=7)` synthesizes a daily digest from the
  episodic layer:
  - **Today** — memories whose event date (`metadata.captured_at` else `created_at`)
    falls on today, with the parsed `HH:MM` time, sorted chronologically (untimed last).
    Surfaces today's meetings and events.
  - **Open commitments** — action items detected across the last `days` days via
    marker phrases (English + Turkish: "I'll / I will / we'll / going to / need to /
    TODO / action item / follow-up", "yapacağım / göndereceğim / halledeceğim /
    takip ed…"). The containing sentence is extracted, de-duplicated, capped at 30,
    most-recent-first.
  - **Might be forgetting** — up to 5 lowest-retention memories (reuses the fading
    computation) to review or pin.
  - **Counts** — today / commitments / fading / recent total.
- **REST**: `GET /api/briefing?days=&project=`.
- **MCP tool**: `briefing` (34 tools total) — a readable TODAY / OPEN COMMITMENTS /
  MIGHT BE FORGETTING digest, or "Nothing pressing" when clear.
- **Dashboard**: new **Briefing** page — three cards (Today, Open Commitments, Might
  Be Forgetting) with a counts header; click any item to open the memory detail drawer.
- 16 new tests (`tests/test_briefing.py`).

## 2.9.0

Timeline — roadmap Phase 2/3 crossover, a chronological view over the episodic layer:

- **Engine**: `timeline(days=30, project=None)` groups episodic memories by the day they
  actually happened — preferring `metadata.captured_at` (a calendar event's or email's
  real timestamp) over `created_at` (when it was captured into StackMemory) — and returns
  day-groups sorted most-recent-first, each with a count and up to 20 summarized items.
- **REST**: `GET /api/timeline?days=&project=`.
- **MCP tool**: `timeline` (33 tools total) — "Last N days: X memories across Y active
  days" digest.
- **Dashboard**: new **Timeline** page — a vertical day-by-day feed; click any item to
  open the full memory detail drawer.
- 8 new tests (`tests/test_timeline.py`).

## 2.8.0

People graph — roadmap Phase 2 (entity layer), MVP grounded in captured metadata:

- **People aggregation** (`server/core/people.py`): distinct people are extracted from
  the structured metadata the capture connectors already store — calendar `attendees`/
  `organizer`, email `from`/`to`/`cc`, transcript `speakers`. Identity merges on email
  (people rename), keeps the most descriptive display name, and counts each person once
  per memory. Pure functions, fully offline.
- **Engine**: `list_people()` and `get_person(query)` (resolves a name/email to a person
  + their memories, most recent first).
- **REST**: `GET /api/people`, `GET /api/people/{key}`.
- **MCP tools**: `list_people`, `about_person` (32 tools total).
- **Dashboard**: new **People** page — searchable list with per-source icons and counts;
  click a person for their memories + a one-click "Ask about X" summary via the Ask engine.
- 8 new tests (`tests/test_people.py`); 180 tests passing.
- Roadmap Phase 2 progress: person graph done; org/project/event entities + timeline next.

## 2.7.0

Transcript connector — the third work-life capture source, completing the Phase 1
capture trio (calendar = when/who, email = correspondence, transcript = what was said):

- **`transcript` connector**: parses meeting transcripts/captions `.vtt` (WebVTT,
  incl. `<v Speaker>` voice tags), `.srt` (SubRip), and `.txt` (Otter/Whisper/plain,
  `Speaker: text`) from Zoom, Google Meet, Teams, Otter, Fireflies. Zero-dependency
  parser, fully offline. Each meeting becomes ONE **summarized** episodic memory
  (`source=connector:transcript`, tags `meeting`/`transcript`) — reusing the project
  summarizer (LLM when `OPENAI_API_KEY` is set, deterministic extractive otherwise) —
  with the speaker list and transcript excerpt in metadata. `summarize=false` keeps the
  cleaned full transcript instead. `transcript_dir` imports a folder (one memory each).
- Wired into the connector registry, REST `/api/connectors/import`, the
  `import_from_app` MCP tool, and the dashboard "Import from Apps" panel.
- 10 new tests (`tests/test_transcript_connector.py`); 164 tests passing.
- Roadmap Phase 1: capture trio (calendar + email + transcript) done; live API sync
  (Gmail/Calendar OAuth) and Phase 2 (entity/knowledge graph) next.

## 2.6.0

Email connector — the second work-life capture source (roadmap Phase 1):

- **`email` connector**: parses `.mbox` (Gmail Takeout, Thunderbird, Apple Mail,
  Outlook export), a single `.eml`, or a folder of `.eml` files using the Python
  standard library (`email`/`mailbox`) — zero extra dependencies, fully offline, no
  IMAP/OAuth. Each message becomes an episodic memory (`source=connector:email`) with
  decoded sender/recipients (RFC 2047), subject, date, and a plain-text body excerpt
  (HTML stripped). Options: `past_days`, `max_messages`, `body_chars`,
  `exclude_senders` (skip no-reply/notification noise).
- Wired into the connector registry, REST `/api/connectors/import`, the
  `import_from_app` MCP tool, and the dashboard "Import from Apps" panel (mbox_path hint).
- 11 new tests (`tests/test_email_connector.py`); 162 tests passing.
- Roadmap Phase 1 progress: calendar + email capture done; meeting-transcript / docs next.

## 2.5.0

Calendar connector — the first work-life capture source (roadmap Phase 1):

- **`calendar` connector**: parses iCalendar `.ics` (local file `ics_path` or published
  `ics_url`) — the universal format Google/Outlook/Apple export. Each event becomes a
  memory with title, time, attendees, organizer, location, and notes; stored as episodic
  with `source=connector:calendar`. Zero-dependency parser (line unfolding, DATE/DATE-TIME,
  attendee CN extraction), fully offline for files, optional window filtering
  (`past_days`/`future_days`).
- Wired into the connector registry, the REST `/api/connectors/import` flow, the
  `import_from_app` MCP tool, and the dashboard's "Import from Apps" panel (with an
  `ics_path` field + hint).
- 11 new tests (`tests/test_calendar_connector.py`); 151 tests passing.
- Roadmap Phase 1 progress: calendar capture done; email/meeting-transcript/docs next.

## 2.4.0

Ask-your-life — the first "second brain" capability (see `docs/ROADMAP-digital-memory.md`):

- **`ask_memory` MCP tool + `POST /api/ask`**: ask a natural-language question and
  get a synthesized answer that cites the exact memories it drew from (with dates).
  LLM-powered when `OPENAI_API_KEY` is set, deterministic ranked-evidence fallback
  otherwise. Read-only — asking never reinforces memories.
- **Dashboard "Ask Your Memory" panel**: question box, example prompts, answer with
  clickable source citations that open the memory detail drawer; `asked` events in the
  live feed.
- 30 MCP tools total. New `server/core/answerer.py`, `engine.ask()`, 9 new tests
  (`tests/test_ask.py`); 140 tests passing.
- Added `docs/ROADMAP-digital-memory.md`: the vision + phased plan to grow StackMemory
  into a personal digital work-life memory.

## 2.3.1-unreleased

Release blocker hardening:

- Moved recall benchmark runtime code into packaged `server.core.benchmark`.
- Kept `scripts/benchmark_recall.py` as a source-tree wrapper only.
- Packaged the built dashboard static export under `server/dashboard` for wheel installs.
- Hardened frontend build by upgrading the Next.js line and eliminating reported production audit findings.
- Added security, contribution, Docker ignore, and public launch checklist files.
- Added packaging and embedder-mode regression tests.

## 2.3.0

StackMemory v2 improved local demo package.
