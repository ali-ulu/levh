# Changelog

## Unreleased

### Fix: recall logging is on by default, so `recall_log` is populated (#376)

- `LEVH_RECALL_LOG` now defaults to on. `recall_log` is the audit substrate
  Phase 2 of `docs/internal/SHARED-MEMORY-DESIGN.md` builds on ("who read this
  memory, and when"), and as an opt-in it held zero rows on a live store: the
  question Phase 2 asks had nothing to answer it from. The privacy cost stays
  bounded — the query is secret-redacted and truncated at 1000 characters, no
  memory content or scores are stored, and retention is capped by
  `LEVH_RECALL_LOG_DAYS` (30 days). An operator who considers typed queries too
  sensitive opts out with `LEVH_RECALL_LOG=0`; `/api/health` and
  `levh recall-report` report whether logging is on.
- `tests/test_recall_log.py` adds `test_a_recall_records_a_row_by_default`,
  `test_the_flag_turns_logging_off`, and
  `test_the_configured_retention_window_reaches_the_recall_path`, which proves
  `LEVH_RECALL_LOG_DAYS` prunes through the recall path rather than only in the
  docs.
- `tests/conftest.py`, `docs/testing.md` and the CI `hostile-env` job plant the
  opposite decoy now (`LEVH_RECALL_LOG=0`), so the behaviour-flag guard still
  fails on an unscrubbed variable instead of planting the new default.

### Feature: query-aware context window with a real token budget

- `get_context(query=...)` now compiles a topic-focused window: candidates are
  ranked by the same `H(x,ψ)` score `recall_memory` uses — vector/FTS candidates
  re-scored with the superseded penalty — and the token budget is filled in
  score order. A memory that is neither recent nor pinned can therefore reach the
  window when it is relevant, which the old recency/pin/importance ordering could
  never do. Without `query` the ordering is unchanged, byte for byte.
- `server/core/tokens.py` replaces the `len(text) // 4` rule of thumb with
  `estimate_tokens`, which weights word characters and punctuation separately.
  Code and identifiers tokenize markedly denser than prose, so the old rule was
  wrong in both directions; this one is still an approximation, but a far closer
  one, and it keeps the storage and recall layers free of any tokenizer
  dependency.
- `get_context_packing()` returns a `ContextPacking` alongside the text: which
  memories the budget admitted, which it displaced, how many tokens were used,
  and which mode produced the window. Ranking quality is otherwise unobservable —
  the text alone cannot show what it crowded out.
- Pinned memories remain mandatory in both modes. The budget governs only the
  optional material, and the assembled text is no longer sliced to the
  *requested* budget's character equivalent — that slice could cut a pinned
  memory out of the text while still listing it as included, which is a bug this
  change fixes rather than preserves.
- `query` is exposed on the MCP `get_context` tool and on `GET /api/v1/context`.
  The frozen contract is regenerated in this commit: `openapi.json` gains only the
  new parameter, and `sdk/typescript/src/generated/endpoints.ts` follows it.
- The ranked path adds no `except Exception` boundary. With a non-semantic (hash)
  embedder it skips the embed/vector work entirely and ranks lexically — the same
  split `recall` already makes, because that cosine is positional rather than
  semantic — so no failure needs swallowing. A real embedder that fails is left
  to surface, which keeps `docs/error-handling.md`'s 61-site boundary count
  accurate.
- `tests/test_context_window.py` — 22 tests: token-estimate properties
  (whitespace-only is zero, code is denser than prose, determinism, the per-memory
  cap), the layered path's unchanged behaviour and session isolation, and the
  ranked path's payoff (a relevant old memory that the layered window misses),
  budget enforcement via `used_tokens`, the included/omitted split, pinned
  survival on a one-token budget, project/session isolation, a blank query
  falling back to layered, and an empty store not raising.
- Four issues found in review of this change, all fixed here:
  - The layered path reported only *pinned* ids in `included` while short-term and
    episodic memories were also in the text, so the packing metadata misdescribed
    the window. It now records admission order for every memory admitted.
  - The ranked candidate pool was only the 50 most recent short-term memories plus
    episodic rows at `min_importance >= 0.5`, so an older or lower-importance
    memory could never be admitted however well it matched — which is the case
    the feature exists to fix. The pool now also takes a lexical scan over the
    in-memory store and the FTS candidates (widened with synonyms), matching
    `recall`'s sources.
  - The ranked predicate omitted the current-row check `recall` applies through
    `_valid_at`, so a retired (superseded) row could enter the window from the
    short-term deque or the vector store. It is now rejected.
  - The per-memory cap charged `MAX_MEMORY_TOKENS` while appending the *whole*
    content, so a memory of ~10,000 tokens was charged 2,000 and contributed all
    of its 45,000 characters: the cap hid the real cost instead of bounding it,
    and `used_tokens` under-reported. The admitted text is now truncated to the
    size the cap represents, in token space (`tokens.truncate_to_tokens`), and the
    assembled string is no longer sliced afterwards.
- `docs/api-reference.md`, `docs/mcp-tools.md` and `docs/mcp-client-config.md`
  describe the parameter.

### Feature: git connector — local repository history as memories

- New `git` connector (`server/connectors/git.py`) reads a local repository's own
  history and turns each commit into one episodic memory led by
  `<short-sha>: <subject>`, with the body, author, email and date in metadata. It
  answers *"who changed this, when, and why?"* from the repository rather than
  from a chat transcript. Distinct from the existing `github` connector, which
  pulls a remote repository's README, issues and PRs over the API: this one reads
  commit history from disk and never touches the network.
- Read-only by construction — `fetch` only ever runs `git log`, so an import
  cannot mutate a worktree, branch or index. No new dependency; it shells out to
  the `git` binary that is already required to have the repository.
- `repo_path` is required and must be the repository **root**. A subdirectory is
  refused with the correct root named in the error: git walks upward from any
  directory, so accepting a nested path would let a directory that merely sits
  beneath some unrelated repository (a temp directory under a home directory that
  is itself a repo, on this very machine) import that repository's entire
  history. `ref`, `max_commits`, `since`/`since_days`, `author`, `include_body`,
  `body_chars` and `timeout_seconds` are configurable; a ref that git would read
  as an option is rejected in `connect()`.
- Registered in the connector registry, so the existing REST routes, MCP tools
  (`import_from_app`, `list_connectors`, `get_connector_help`) and CLI `sync`
  pick it up with no route or tool changes — `openapi.json` is therefore
  unchanged, which `tests/test_openapi_contract.py` confirms.
- `tests/test_git_connector.py` — 21 tests: log parsing (including bodies with
  newlines and malformed records), one memory per commit, newest-first ordering,
  `max_commits`, `include_body`, `body_chars`, `author` filtering, the
  root-only rule and its "sibling directory must not import the outer repo"
  regression case, option-like ref rejection, a bad ref degrading to an empty
  list rather than raising, an empty repository, and an end-to-end import
  through `/api/connectors/import`.
- `docs/connectors.md`, `README.md` and `docs/ARCHITECTURE.md` list the new
  connector.
- No issue number on this heading: the connector was not previously tracked, and
  the maintainer explicitly waived the issue requirement for it rather than
  delaying the change behind an issue describing work already done. The commit
  message records the same.

### Refactor: convert the error boundary, token gate, result card and timeline to the i18n catalogue (#308)

- Four user-visible surfaces join the catalogue as follow-on conversions of
  #308: `src/app/error.tsx` (3 → 0), `src/components/auth-gate.tsx` (5 → 0),
  `src/components/memory-result-card.tsx` (4 → 0) and `src/app/timeline/page.tsx`
  (7 → 3). Each lowers its own ratchet entry in
  `frontend/scripts/ui-string-baseline.json`; the gate now stands at 38 files
  and 693 literals, down from 41 and 709.
- The timeline count is composed through `app.timeline.count.one` /
  `app.timeline.count.other` with a `{count}` placeholder rather than two
  literals, matching the interpolation-only message format the mechanism chose.
- The 3 residual hits on the timeline page are deliberate: `en-GB` and `2-digit`
  are `Intl.DateTimeFormat` option values and `connector:` is a source-id prefix
  stripped from `item.source` before display, not copy. A comment records why so
  a later pass does not "convert" them.

### Docs: record the commercial-surfaces decision rule (#298)

- Design issue #298 left the boundary between the four candidate commercial
  surfaces open. It is now settled by one test — does the surface need a second
  principal? — and recorded in `docs/internal/COMMERCIAL-SURFACES-DESIGN.md` so
  the decision is a fact in the tree rather than an issue thread.
- Adopt support/SLA/signed build now (no engine change); approve single-user
  hosted sync in principle as zero-knowledge and published-but-not-operated;
  route the team workspace and SSO/metering to the phase 2–4 triggers in
  `SHARED-MEMORY-DESIGN.md` now that #302's phase 1 landed. No runtime code
  changes.

### Feature: tenancy boundary as the degenerate single-user case (#302)

- The shared/team memory design landed in #362 with four open questions; they
  are now decided (one deployment with many workspaces, `project` stays a label
  inside a workspace, an agent principal belongs to the workspace, and phase 1
  is worth doing now) and phase 1 is implemented against them. Nothing
  user-visible changes: a single-user install is the degenerate case of one
  implicit workspace (`default`) and one implicit principal (`local`).
- `server/core/tenancy.py` carries the principal and workspace in a
  `ContextVar`, the same mechanism `request_context` already uses, so the
  storage layer resolves its boundary without every signature growing a
  parameter. `request_id_middleware` (in `server/middleware.py`) binds the
  local principal per request — the seam a later account layer plugs into.
- `memories` gains `workspace_id` (schema v4, additive, indexed) and every
  read/write in `server/core/db/` is scoped to it. A v3 store is migrated and
  every existing row is backfilled into `default`; nothing is dropped. The
  store stamps the boundary on write and ignores a caller-supplied
  `workspace_id`, so a request cannot choose its own workspace.
- The in-process vector store and short-term deque still mirror the whole
  store, and recall filters candidates by workspace — a partial mirror would
  silently make a row unrecallable. The store-wide maintenance passes (re-embed,
  demo purge) bind each row's own workspace around their write, since the scan
  spans workspaces while the write is scoped. `tests/test_workspace_tenancy.py`
  pins the single-user round trip unchanged and the cross-workspace
  invisibility of reads, counts, updates, deletes, recall and supersession
  cleanup.

### Fix: keep the committed LoCoMo artifact version in sync with releases (#361)

- `tests/fixtures/external_benchmark/locomo10_retrieval_hash.json` records the
  package version it was produced by, but `scripts/release.py` did not treat it
  as a version site and no test compared it. Once `pyproject.toml` was bumped,
  the artifact's `levh_version` silently went stale while
  `docs/memory-evaluation.md` still promised a byte-identical fresh run.
- The fixture is now a `VERSION_SITES` entry, so a release bump rewrites it;
  `assert_consistent` and
  `test_committed_artifact_is_well_formed_and_content_free` assert it matches
  the current package version, and a focused release-pipeline test reproduces
  the stale-artifact failure.

### Feature: frontend i18n extraction mechanism + drift gate (#308)

- The design issue asked for the extraction mechanism before any page was
  converted, because the choice decides how much of the ~20 pages has to be
  touched twice. It is now settled: a flat, key-addressed catalogue
  (`frontend/src/lib/i18n/en.json`) behind a `useT()` hook, with no runtime
  library and no ICU message format. Locale is a client value stored in
  `localStorage`, **not** a route segment, so the static export under
  `server/dashboard` is unchanged and does not multiply per locale. The
  decision and its trade-offs are recorded in
  `docs/internal/I18N-DESIGN.md`.
- A proof conversion lands the mechanism on one page (`settings`), one section
  (`access-token`) and one shared primitive (`ui/dialog`, whose close-button
  `aria-label` and screen-reader text were hardcoded). Its strings moved into
  the catalogue; nothing else was converted.
- `frontend/scripts/check-ui-strings.mjs` is the drift gate the issue called
  for: an AST scan that fails when a user-visible literal appears outside the
  catalogue. Because the tree is not converted yet, the gate is a *ratchet* —
  per-file counts in `frontend/scripts/ui-string-baseline.json` may only go
  down, so a new hardcoded string fails CI and a conversion must tighten its own
  entry. It runs in `npm test`, which the `frontend` CI job already runs.
- Only `en` ships. No second locale is promised; the mechanism makes adding one
  a data change rather than a refactor.

### Refactor: convert the app chrome to the i18n catalogue (#308)

- The chrome every page renders is now catalogue-driven: `theme-switcher`
  (6 → 0, labels composed with `{theme}` interpolation), `sidebar` (27 → 2) and
  `header` (30 → 3). The nav model carries `labelKey` instead of English copy.
- The residual hits are deliberate, not missed: the sidebar's `LEVH` brand and
  version badge are matched by `scripts/release.py` and must stay literal, and
  the header's `INPUT`/`TEXTAREA`/`SELECT` are DOM tag names compared against
  `event.target.tagName`, not copy.
- `src/app/layout.tsx` is left unconverted: its strings live in the Next.js
  `Metadata` export, evaluated at build time outside any client component, so it
  needs its own mechanism rather than the `useT()` hook.
- The drift gate now skips a literal that is exactly a catalogue key, so keys
  held in a data model are not counted as copy; the exception is non-heuristic
  (the string must exist in `en.json`) and is pinned by a detector test.

### Feature: retrieval-only external benchmark (LoCoMo) for the self-authored harness (#340)

- `levh benchmark-locomo` measures LEVH against the public LoCoMo benchmark
  using retrieval-side metrics only — no LLM judge, so it stays offline and
  deterministic. The self-authored golden-fixture evaluator can only tell you
  whether a change regressed *our* scenarios; this is the external yardstick
  the issue asked for.
- `server/core/external_benchmark.py` feeds each conversation through the real
  admission gate and `recall`, one memory per turn, and scores recall@k / MRR
  against the turns LoCoMo labels as evidence. The dataset is not vendored and
  never downloaded — `--data` points at a checkout.
- The adversarial category is reported as an **evidence-retrieval proxy**, not
  an abstention rate: LoCoMo does not label an adversarial question as absent
  from the conversation and boolean recall cannot decline. Answer abstention
  needs an LLM judge and is deliberately out of scope.
- Turns are pinned so wall-clock decay cannot flip near-ties, which makes a
  hash-embedder run byte-identical; the committed artifact
  `tests/fixtures/external_benchmark/locomo10_retrieval_hash.json` is
  reproducible. It records a lexical floor (hit@1 0.3581, MRR 0.4486 over 1536
  questions), not a semantic result.

### Fix: ed25519 envelope verification from a private key file (#338)

- `server/core/federation.py::_load_ed25519_public` now calls
  `private_key.public_key()` to derive the public half from a private PEM.
  Returning the bound method itself made `verify_envelope` raise
  `AttributeError` when the receiver held the sender's own key file — the
  documented single-file case — instead of verifying the envelope.

### Feature: signed federation envelope — provenance-verified export/import (#338)

- New `server/core/federation.py` signs a `full_export` bundle into a
  self-describing envelope (node id + algorithm + signature) with
  `hmac-sha256` (shared secret) or `ed25519` (operator-held PEM key). Any
  tampering invalidates the signature and `verify_envelope` rejects the whole
  envelope rather than importing part of it.
- New CLI pair `levh federation-export` / `levh federation-import`. Import
  verifies the signature first and only then feeds the bundle's memories
  through `import_memories_gated`, so the admission gate stays the boundary
  for untrusted peer input. Offline only — no transport in this slice.

### Fix: drop the test-only `_count_quarantined_rows` wrapper (#354)

- `server/commands/doctor.py` no longer carries `_count_quarantined_rows`: it
  was never called in production, which counts quarantined rows through
  `_quarantined_rowids` directly. The test that used it now calls the same
  function production does, so the two cannot drift.

### Fix: the env template gate reads `get_env`, and the template lists what it finds (#351)

- `tests/test_docs_match_code.py::_referenced_env_names` now also resolves
  `get_env(...)` call sites — a literal or a module-level string constant —
  and normalizes each through `accepted_env_var_names`, so a variable read
  only under its bare spelling still reaches the comparison. The gate was
  green while `.env.example` omitted `LEVH_SQLITE_DB_PATH` and
  `LEVH_SYNONYMS_PATH` because it only looked for `LEVH_*` literals.
- `.env.example` gains `LEVH_SQLITE_DB_PATH` (canonical alias of the bare
  `SQLITE_DB_PATH` it already listed) and `LEVH_SYNONYMS_PATH`, which
  `docs/configuration.md` and `docs/ARCHITECTURE.md` document but the template
  never surfaced.

### Fix: mypy ratchet gap — `procedure.py` and `guard.py` (#350)

- `MemoryEngine` now declares `db: Database` and `episodic: EpisodicMemory` at
  class level. Both are assigned in `wire_engine`, but the mixins that hold
  them are hidden from mypy by `follow_imports = "silent"`, so a module typed
  against `MemoryEngine` could not see `engine.db` or `engine.episodic`.
  Naming the two storage collaborators at the class level lets the type
  checker resolve them without changing how the engine is wired.
- `server/core/procedure.py` and `server/core/guard.py` join the
  `[tool.mypy].files` ratchet, so the three previously-invisible
  `attr-defined` errors now fail CI if they return. The ratchet only grows.
### Feature: bi-temporal validity — retire superseded facts, keep them auditable (#335)

- A memory now carries a **world-time validity interval** alongside its
  system-time timestamps. `valid_from` opens at write; `valid_to` closes when
  a newer memory supersedes it. A retired fact is no longer *current*, so it
  drops out of ordinary reads — but the row is never deleted, stays auditable,
  and is reachable through a point-in-time read.
- `POST /api/memories/recall` and the `recall_memory` MCP tool take `as_of`
  (an ISO-8601 instant) to ask "what did the store believe then", and
  `include_superseded` to audit what a current fact replaced. `GET
  /api/memories` shares the same filter, so a retired fact cannot leak back in
  through the list surface. An `as_of` read is always read-only: a question
  about the past must not reinforce a belief the store holds now.
- **Retirement is opt-in** (`LEVH_SUPERSESSION`, default off). This is the
  point the issue is emphatic about: forgetting and supersession are different
  problems. The write path already weakens a whole same-project neighbourhood
  (wide interference), and turning every such hit into a retirement would hide
  rows a user never superseded — it broke dedupe/consolidation in the first
  cut of this change. So weakening stays as it was, and only the flag turns a
  near-identical write into a retirement.
- Known limit, recorded in `_retirement_enabled`: the candidate key is
  near-identical content, which cannot distinguish a genuine one-value edit
  ("...is main" → "...is prod", lexical overlap 0.75) from a templated
  enumeration ("...number 3" → "...number 4", overlap 1.00). With the flag on,
  a templated pair retires like a supersession. Acceptable for a prototype an
  operator turns on deliberately; not acceptable as a silent default, which is
  what the flag encodes.
- Schema v3 adds `valid_from`/`valid_to`/`superseded_by` (additive, nullable)
  and backfills `valid_from` from `created_at` on connect. The legacy
  `metadata.superseded_by` pointer is deliberately **not** backfilled into
  `valid_to`: that pointer is the wide interference signal, not a retirement,
  so old rows start current. Deleting a replacement reopens its predecessor's
  window, mirroring the existing metadata-pointer cleanup.

### Fix: a global guard rule reaches a project-scoped check (#337)

- `list_rules` passed `project` straight through to `search_memories`, which
  filters `memories.project = ?` exactly — so a rule recorded *without* a
  project was invisible to a project-scoped `check_action`, and the gate could
  return `allow` for the one mistake that applies everywhere. A global rule is
  the most general kind, not the least.
- The merge is done **in the query**, not on the results. `search_memories` now
  takes `include_global` and widens `project = ?` to
  `(project = ? OR project IS NULL)` inside the same `WHERE`; `episodic.search`
  forwards it; `list_rules` sets it. Filtering a fetched page in Python would
  have been wrong: the query applies a `LIMIT`, so a page of pinned memories
  from other projects — none of which carry `RULE_TAG` — would have filled it
  and the applicable global rule would never have reached `check_action`.
- The flag defaults to `False`, so exact project filtering is untouched for
  every other memory search; the guard is the one caller that opts in.
  `include_global=False` still gives a strict single-project view.

### Feature: pre-action judgment gate — check a proposed action against recorded rules (#337)

- `guard.py` recorded a corrected mistake as a pinned rule plus a violation row,
  and its docstring drew an explicit line: deciding whether a *proposed* action
  violates a rule is a different problem, needing a latency budget and a
  false-positive story. `check_action` is the first thing to cross that line,
  on the terms the docstring set.
- The matcher is **deterministic and model-free** — the same lexical,
  stem-aware overlap `lexical.py` and `conflict.py` already use. No network, no
  LLM, nothing on the hot path that can fail closed. It lives in
  `server/core/action_gate.py` as a pure function, so the decision is testable
  without a database.
- The verdict is **advisory**: `warn` or `allow`, never `block`. A warning says
  "this overlaps something you got wrong before"; whether that is an
  instruction stays the caller's policy, so the gate does not become a
  permission system.
- Matching is narrow on purpose. A rule's `wrong_action` and its `task` are
  scored **separately** and the stronger wins, so a terse sharp rule is not
  diluted by a verbose task; a match needs at least two shared content words
  and 60% coverage of one description. An unrelated action is silent, which is
  the property that keeps the one warning that matters from being dismissed
  with the rest.
- Read-only: it touches no counter, decay clock or violation row, so asking a
  question cannot change the answer it reads.
- Surfaces: `POST /api/guard/check`, the `check_action` MCP tool (in the
  `work` profile — a gate consulted only in admin sessions is consulted too
  late), and `GuardService.check_action`.
### Feature: procedural memory — propose repeatedly-reused memories as skills (#339)

- `MemoryType` has two values, `short_term` and `episodic`; the taxonomy's third
  and fourth, semantic and procedural, were missing. This lands procedural, and
  it lands it the way the research the issue cites does: **a skill is not stored
  the first time it is seen — it is verified by repeated successful reuse before
  it is promoted.**
- The raw material was already in the store and unread. `memories.recall_count`
  and `memories.frequency` count reuse and nothing consumed them as a signal;
  `violations` records a rule that failed. `server/core/procedure.py` reads
  those three counters and nothing else — no model, no network, no new schema —
  which is what makes the rule a pure function and the whole path offline.
- **A tag, not an enum value.** Promotion marks a memory `levh-procedure`.
  Adding a `MemoryType` member would touch the model, the schema's CHECK
  constraint, the docs gate and the MCP surface for a distinction the store does
  not need to enforce — and `RULE_TAG` is the precedent for exactly this.
- **Proposed, never promoted.** Nothing pins a memory, clears its decay clock or
  changes its type. A candidate becomes a row in the findings inbox — the same
  "signal, not verdict" surface `conflict.py` and the admission gate's `review`
  verdict use — and a person promotes it. The *proposal* is a function of the
  counters; the *promotion* is a decision this layer never makes.
- Three exclusions, each with a reason: a **pinned** memory is already exempt
  from decay; an **already-tagged** memory is a decision a human made; a memory
  with a **recorded violation** is not a success story. The last is the only
  clause that can fail a memory the counters alone would pass — it is the
  "no violation recorded against it" requirement, and it is also the whole
  demotion story: a procedure that later fails is caught by the same rule.
- The thresholds are all three together (`recall_count >= 3`,
  `frequency >= 3`, `importance >= 0.5`), not any: a memory recalled three times
  in one session is a hot query, not a skill.
- Ships with the **"skill promotion"** golden fixture the issue asked for: a
  memory reused three times with no violation becomes a proposed procedure; one
  reused once does not; one with a violation does not. The evaluation report
  gains a `procedures` surface counting *proposals*, never promotions.
- The rule is deliberately not on the librarian loop yet. The proposal path is
  the deliverable; putting it on a timer is a follow-up, though the finding
  fingerprint already folds a repeated proposal into one row (it bumps
  `occurrences` rather than filing a duplicate), so a loop would not spam the
  inbox.

### Feature: recall quality from the store's own recall log (#336)

- `recall_log` has recorded every recall's ranked result ids since it landed,
  with a docstring saying the number it exists to produce is *"of the memories
  I was handed, how many were worth having"* — and nothing computed it. The new
  `levh recall-report` does, offline and deterministically.
- The score is an **existence-and-retention proxy**, not precision against
  labelled relevance, and it is not a bound in either direction: a returned
  memory counts as a hit when it still resolves in `memories` and its question
  has not since stopped returning it, so an irrelevant-but-live result scores
  as a hit, and a memory re-ranked away counts as churn. The report says all of
  this in its own `limits` field rather than implying a labelled score; read the
  trend across runs on one store, not the absolute number.
- Churn is keyed on query text, project and top_k, so the same question under a
  different project or window cannot contaminate it. `session_id` is
  deliberately excluded — the store is shared across sessions, and
  "this question stopped returning this memory" is a store-level fact.
  `min_importance` is not stored in `recall_log`, so it is a noted deferral.
- `server/core/recall_quality.py` is pure (`build_recall_report` takes rows and
  an id set, no DB and no clock), so the report is byte-identical across runs;
  the window is the log's own oldest/newest row, never the time it ran.
- `--json` prints the full report; `--output` also writes it to disk, with its
  status line on stderr so stdout stays valid JSON for a pipe. An empty log
  prints "no recalls logged", not a precision of 0.0 — nothing was asked is not
  everything failed.

### Feature: response schemas for the full REST surface (#307, #309)

- The published contract declared request bodies precisely but left every `200`
  response an empty object, so SDK clients could only receive `unknown`. The
  full REST surface now declares what it returns — 108 of 110 operations
  across sessions, entities, findings, agents/checkpoints, attachments,
  conflicts, connectors, knowledge (people/organizations/timeline/briefing/
  meeting-prep), guard, memory extras (dedupe, held memories, secrets,
  trust, import/export), system, librarian and onboarding. The two untyped
  operations have no JSON body (binary backup/metrics exports). Each shape
  was verified against the live handler response field-by-field rather than
  guessed.
- `scripts/export_openapi.py` is the regeneration counterpart of the contract
  freeze test, so a deliberate schema change can be committed with its
  contract update in one step.
- The TypeScript SDK regenerates with ~120 new response types; untyped
  operations keep the honest `unknown` default.

### Fix: make the SAST nosec staleness test pass on Windows (#103)

- `test_sast_nosec_annotations_all_still_suppress_a_live_finding` compared
  bandit's `filename` field against the annotation scan's posix-normalised
  paths verbatim; on Windows bandit reports `server\\commands\\doctor.py`, so
  every real finding mismatched and all nosec annotations were falsely
  reported stale. The comparison now normalises both sides through
  `Path.as_posix()`.

### Fix: bound the write-storm rebuild-count assertion to the hot loop it
exists to catch (#103)

- `test_write_storm_does_not_retry_per_write` asserted `attempts <= 10`, but
  the debounce admits ~10 retries per second by design and a loaded runner
  measured 13 in its storm window — a timing flake, not a hot loop. The
  hot loop the test exists to catch is one attempt per write (50); the
  bound now asserts that: `attempts < 50` and `warnings < 50`.

### Fix: a row stored without an id can be repaired, not only reported (#324)

- `levh doctor` counted quarantined rows and stopped there. The rows that most
  need naming are the ones with no `id` to name them by, and every deletion path
  takes an id, so the warning described a loss with no way out.
- It now names the quarantined rows by rowid, and `levh doctor --fix-ids` gives a
  row whose only defect is a missing id a generated one. The memory comes back to
  recall and becomes reachable by `levh purge`; the stale FTS entry keyed on the
  NULL is dropped in the same write. A row rejected for any other reason keeps its
  content and its type untouched — a missing id is the one defect this repairs.
- Doctor still writes nothing without the flag, and the repair waits at most 5 s
  for a store a live server is writing to rather than hanging the command an
  operator runs to find out what is wrong.
- Two new `except Exception` boundaries (a failed repair, an optional hint), so
  the count in `docs/error-handling.md` moved 59 to 61.

### Perf: recall searches a matrix, not a restacked copy per call (#78)

- `VectorStore.search` rebuilt `np.stack([...])` over every candidate on every
  call — an O(n) copy per query, on top of a per-query norms pass and a full
  `argsort`. At 20K 384-d vectors an unfiltered top-k search cost ~31 ms and a
  filtered one ~35 ms.
- Each dimension now keeps a normalised, capacity-doubling row matrix plus an
  `id -> row` map, so a search is one `matrix @ query` product; ranking above
  the cut uses `argpartition` (O(n)) and only the survivors are sorted. The
  same 20K search now costs ~0.8 ms (about 40x) and a filtered search ~9 ms
  (about 4x); the predicate still runs per row, unchanged.
- Behaviour is preserved: the same cosine scores, the same
  predicate-before-ranking contract, and mixed-dimension tolerance via
  per-dimension buckets. Removal is O(1) (tail swap) and re-adding an id
  replaces its row in place.

### Feature: an empty recall says why it was empty (#78)

- An empty result has several indistinguishable causes — no memories at all, a
  filter that excludes every match, or a query whose wording appears nowhere —
  and they call for different fixes. `RecallResult` now carries a
  `RecallDiagnosis` when nothing was returned: `stored_total`,
  `in_scope_total`, a per-filter exclusion count, the query terms actually
  matched on, and plain-language `reasons`.
- The counts come from the same table the candidate pipeline reads, so the
  reason is a fact about the store rather than a guess about which stage
  dropped the row. A wording mismatch is reported ahead of a filter, because a
  filter is not what emptied a recall that could match nothing anywhere.
- Exposed on `POST /api/memories/recall` as `diagnosis` (null when the recall
  returned something) and as a one-line "Why:" in the `recall_memory` MCP tool.
- Model-free and deterministic: no embedder is consulted for the diagnosis.

### Fix: a differently scoped fact no longer demotes the real one (#78)

- Model-free supersession scored overlap one-way, the same shape as recall's
  query coverage. That let a memory which merely *contains* another's words
  count as a replacement: "the production deploy branch is prod, not main" was
  marked superseded by "the staging deploy branch is stage, not prod", because
  the near-miss answers every word of the real fact while the real fact does
  not answer every word of the near-miss. The real answer was then demoted and
  dropped out of the top hit.
- Supersession now scores symmetric coverage (`lexical.mutual_similarity`, the
  minimum of both directions) and the floor sits above the topic-frame overlap
  (~0.6) that a differently scoped fact produces, so only a genuine
  restatement or one-value edit is treated as a replacement.
- Recall's ranking coverage is unchanged; only the write-path supersession
  test moved.
- On the hardened benchmark corpus this restores `hit@1` from 0.667 to 0.714
  and `mrr` from 0.778 to 0.802, so the floors move with it and the gate holds
  the real fact at the top.

### Feature: a superseded fact ranks below the one that replaced it (#78)

- Writing a near-identical memory weakens the older one (retroactive
  interference), but weakening only lowers the score the old memory *will*
  have: it decays faster from then on, while today's ranking is untouched. The
  replaced fact still tied with its replacement and could outrank it on
  importance or access frequency — "forgetting works, but the old answer keeps
  coming back first."
- The write path now records the supersession on the older memory
  (`metadata.superseded_by` / `superseded_at`), and recall adds a small,
  bounded penalty so the current fact wins. The penalty is smaller than the
  gaps the four weighted terms produce, so it breaks a tie or a small lead
  without letting a stale fact beat a genuinely better match.
- The demotion is shown in the `explain` breakdown as `superseded_penalty`, and
  the single-memory score route applies the same term, so the two surfaces
  agree on the score a memory ranks with.
- Deleting the replacement clears the pointer in SQLite and in the in-memory
  copies recall actually scores, so a removed replacement cannot leave its
  predecessor demoted.

### Feature: the recall-quality gate watches a corpus that can actually fail (#78)

- The gate was real but toothless: the corpus was easy enough that every metric
  sat at 1.0, so no ranking regression could ever move a number below its
  floor. The floors were the corpus's own perfect score, not a quality bar.
- The corpus now mixes exact-match queries with paraphrases that share no
  content word with the stored memory — reached only through the synonym layer
  or the language-agnostic stem — and adds near-miss distractors that carry a
  query's vocabulary but answer a different question (a staging branch beside
  the production one, an admin-panel password beside the API's JWT).
- The floors are the real numbers the pipeline now achieves (`hit@1` 0.667,
  `hit@3` 0.905, `hit@5` 0.905, `mrr` 0.778 on 21 queries), below 1.0, so a
  weight tweak or a candidate-source change that reorders results fails the
  build instead of passing unnoticed.

### Feature: recall learns the words you might use instead (#78)

- Model-free ranking matches the words of the query against the words of a
  memory, which finds an inflection (`migrasyon` → `migrasyonu`) but not a
  synonym: a memory about JWT authentication shared no surface word with "how
  do users log in" and so could never become a candidate, however well it
  answered the question. A query term is now expanded with the terms the store
  considers equivalent, and the expansions take part in candidate retrieval
  and in the lexical score.
- Sources merge: a small built-in map for equivalences that cost nothing to
  know, and a vocabulary file. `LEVH_SYNONYMS_PATH` points at a store's own
  file; unset means the vocabulary shipped with the package
  (`server/data/synonyms.json`), so expansion works out of the box. The file
  is cached by mtime, so editing it takes effect without a restart.
- Multi-word members match as phrases, so "log in" reaches its group even
  though the tokenizer also sees "log" and "in".
- The table is deliberately not applied to the duplicate check: two memories
  that merely use different words for the same idea are not duplicates, and
  widening that comparison would start dropping real memories.
- Recall also bridges to the entity graph: a query term that names an entity
  pulls the memories linked to it, labelled `entity` in the `explain`
  breakdown. The graph already stored this; it was only ever exposed as its
  own endpoint, so recall could not use it as a candidate source.

### Feature: the recall-quality benchmark is a CI gate, not just a report (#78)

- `levh benchmark --check` exits non-zero when a gated metric is below its
  floor, and CI runs it on Python 3.12. Every unit test can pass while
  ranking quality quietly drops — a weight tweak or a candidate-source change
  moves hit@k without breaking an assertion — so this is the only thing
  watching the numbers.
- Only the model-free (`hash`) mode is gated. It is deterministic, so the same
  corpus yields the same numbers on every runner; a semantic mode's numbers
  depend on the installed model and are not gated. Floors live in
  `server/core/benchmark.py::QUALITY_FLOORS`.

### Feature: hybrid retrieval — full-text candidates join the vector store (#78)

- The vector store is process-local and only ever holds rows that had an
  embedding. A row without one — imported by a peer, or left vector-less by a
  mode switch — was invisible to every candidate path, so recall could never
  return it however well its words matched. Recall now also asks SQLite's FTS5
  index, which is keyed on content and reaches those rows regardless of
  vectors; the database is the source of truth and it was sitting unused as a
  recall candidate source.
- FTS candidates are ranked by bm25, then re-ranked by H(x,ψ) like every other
  candidate, and are labelled `keyword` in the `explain` breakdown.
- In semantic mode a candidate reached only by FTS has no vector the query can
  compare against, so it is scored on term coverage and reported as such
  rather than as a measured-looking cosine of 0.
- Query filters (project/session/importance) gate the FTS source too, and a
  runtime whose SQLite lacks FTS5 simply contributes no FTS candidates —
  recall degrades to the previous vector-only behaviour.

### Feature: recall can explain why a memory ranked where it did (#78)

- A recall score is a weighted sum of four penalties, so the number alone
  cannot separate a memory that *matched poorly* from one that *matched well
  but decayed*. Those call for opposite responses — rephrase the query vs.
  re-read and reinforce the memory — and "it forgot" versus "it never matched"
  is exactly the ambiguity behind the recall complaints.
- `recall(explain=True)` (REST `explain`, MCP `recall_memory(explain=true)`)
  returns, per result: which signal drove the ranking (lexical in model-free
  mode, cosine with a real embedder), the raw cosine even when it did not rank,
  where the candidate came from (`vector` or `keyword` — a stale vector the
  query dimension skipped is found by terms and labelled), and the four
  components that sum to the score.
- Off by default, and the ranking is identical either way; the components are
  already computed on this path, so there is no extra work — only a larger
  payload when asked for.

### Feature: `levh reembed` repairs vectors left in a previous embedder's space (#78)

- Switching `EMBEDDER_MODE` (`hash` → `local`/`openai`/`ollama`, or back) leaves
  every stored vector in the *old* embedder's space. Recall compares only
  vectors whose dimension matches the query and silently skips the rest, so the
  pre-existing memories stop being reachable — the same "it never remembers"
  symptom from a second cause. `levh doctor` warned about this but offered no
  way out.
- `levh reembed` re-derives each vector from its content, the only input the
  embedder ever saw, and needs no source beyond the store. `--dry-run` reports
  the stale set (with a per-project breakdown) without touching anything;
  `--project` narrows the run.
- Staleness is judged by `metadata.embedding_provenance`, written on every
  store/update: a memory is stale when its recorded provider/model/dimension
  differs from the active embedder. Re-running is a no-op once the store
  matches.
- The engine mixin (`server/core/engine/reembed.py`) updates the live vector
  store in the same step as the DB row, so a running server does not keep
  serving the old vector until its next reload.
- `levh doctor`'s dimension warning now names the command.

### Fix: model-free recall is language-agnostic, so inflected queries match (#78)

- Symptom: model-free recall (the previous entry) ranked on exact word
  overlap, which assumes every term is stored and queried in the same surface
  form. Turkish, a first-class store language, inflects: a memory stored as
  "Veritabanı migrasyonu ... çalışır" was missed by "migrasyonlar ne zaman
  çalışıyor", and "deploy" was missed by "deployu".
- Fix: `server.core.lexical` now treats two words as the same term when they
  share a long enough opening — a language-agnostic stand-in for stemming, with
  no stemmer, dictionary or per-language table. `migrasyonu`/`migrasyonlar`,
  `deploy`/`deploying` match; `config`/`confirm` share three characters, below
  the four-character floor, and do not. The Turkish function words ("bir", "ve",
  "ile", "bu", …) join the stopword list so they cannot manufacture overlap.
- The trade is deliberate: short stems over-cover on purpose, because recall
  scores query coverage, where a false positive costs a little precision while a
  false negative hides a memory the user asked for.
- `server/core/benchmark.py` grows three Turkish rows plus two Turkish
  distractors and a Turkish end-to-end recall case in
  `tests/test_lexical_recall.py`; hit@1 stays 1.0 across both languages.

### Fix: model-free recall answers, and the hash gate stops losing new memories (#78)

- Symptom: with no embedding model installed (the default `auto` mode when
  `sentence-transformers` is absent, i.e. every plain `pip install levh`), a
  memory could be written without error and then not come back from recall.
  `forget` worked; remembering did not.
- Cause, two halves:
  1. The `hash` fallback embedder is positional-char based, so its cosine
     measures character positions, not meaning. A memory whose every content
     word matches the query could rank below noise that merely shares a prefix,
     so recall returned the wrong entries.
  2. That same cosine fed the admission gate. Unrelated sentences score ~0.83
     and distinct facts sharing a prefix cross the 0.90 "possible duplicate"
     threshold, so genuinely new content was answered `review`, parked in
     `held_memories`, and never admitted — invisible to recall.
- Fix: `Embedder.is_semantic` states whether the resolved mode yields
  meaning-bearing vectors (only `hash` is `False`), and every decision that
  treated cosine as meaning branches on it:
  * recall ranks model-free mode on query word-overlap
    (`server.core.lexical`) and pulls keyword candidates the positional cosine
    missed;
  * the admission gate narrows to *exact* duplicates under `hash` (a
    byte-for-byte re-store still rejects) instead of the near-duplicate band;
  * retroactive interference uses a lexical floor instead of the cosine
    threshold, so a superseding edit still weakens the older memory while an
    unrelated one does not.
  Under a real embedder (`local`/`ollama`/`openai`) the cosine is used exactly
  as before. No vector is rewritten and no migration is needed — the same
  `hash` vectors are stored, they are just not trusted for ranking.
- Measured on `server/core/benchmark.py` under `EMBEDDER_MODE=hash`:
  hit@1 0.5 → 1.0, MRR 0.608 → 1.0.
- Covered by `tests/test_lexical_recall.py`: five distinct facts stored and
  recalled with no model, keyword rank beating positional noise, an exact
  duplicate still rejected, and supersession still weakening the older memory.

### Connectors: Jira and Linear, pull-on-demand

- Two connectors were registered but did not exist: the report's connector list
  named Jira and Linear, and `docs/connectors.md` promised them nowhere. Both now
  exist, follow the `github`/`notion` shape (httpx, no SDK), and are registered in
  `server/connectors/__init__.py`, so they appear in `list_connectors()`, the
  `/api/connectors` routes, and `import_from_app`.
- `jira` uses Jira Cloud REST API v3. The legacy `GET /rest/api/3/search` is
  deprecated in favour of `/rest/api/3/search/jql`, and the default JQL is
  `updated >= -90d` — a first sync must not walk an entire site by accident.
  Atlassian Document Format descriptions are flattened to text, because a nested
  ADF tree stored as-is is unsearchable.
- `linear` uses the GraphQL API with cursor pagination; `team_ids` and
  `project_ids` translate into Linear's `IssueFilter`.
- Both are capped at 100 issues by default and stop on a short page, so a single
  invocation is bounded. Sync is one fetch per call — LEVH has no scheduler, and
  the roadmap's "pull-on-demand or worker" question is answered by the framework
  that already exists: on-demand.
- Covered by `tests/test_jira_linear_connectors.py`, which drives the real
  connector code against `httpx.MockTransport` and asserts the outgoing request
  (auth header, JQL, filter, cursor) as well as the parsed memory.

### TypeScript SDK, generated from the committed contract

- The roadmap deferred a TypeScript SDK on one question: generate it from
  `openapi.json` or hand-write it. It is generated. The contract is already
  frozen by `tests/test_openapi_contract.py` and is the thing clients are told
  to code against, so a generator reading the contract cannot drift from the
  published API the way a hand-written client would.
- `scripts/generate_sdk.py` emits `sdk/typescript/src/generated/{types,endpoints}.ts`
  from `openapi.json` alone. `scripts/release.py` now bumps the SDK's
  `package.json` too, and `assert_consistent` checks it, so a release cannot
  leave it behind.
- What the contract supports is the interesting part: it declares request
  bodies but **no response schemas** — every 200 is an empty object across all
  102 operations. So the SDK's request types are exact and its responses are
  `unknown`. A generated `interface MemoryResponse` would compile and then
  silently stop matching the server; `unknown` is the honest answer, and
  `tests/test_typescript_sdk.py` fails the day response models appear, as the
  prompt to tighten it.
- The client has no dependencies and no build step: Node 22.6+ strips the type
  annotations itself, so `node --test` runs the shipped `.ts` files. A
  published package that a caller can audit in an afternoon.
- Covered by `tests/test_typescript_sdk.py` (drift, determinism, version,
  operation-table completeness) and `sdk/typescript/src/client.test.ts`
  (12 tests over URL building, headers, body serialisation and error mapping).

### Pages: retire the GitHub Pages deployment (#103)

- `deploy-pages.yml` published `docs/` to Pages, and the custom domain
  `levh.ai-ulu.com` was never claimed — the site answered 404 on the domain
  while `ali-ulu.github.io/levh/` worked, and `pyproject.toml` shipped both as
  the package Homepage and Documentation URL. The deployment is removed and the
  three places that pointed at it now point at the README: the PyPI project
  URLs, the README badge, and the landing page's `og:url`.
- `docs/index.html` is kept as the landing page source so it can be hosted
  elsewhere later; nothing in the repo serves it now.

### Dashboard: serve an export that carries its bundles (#103)

- A fresh checkout served a blank dashboard. `frontend/out` is committed as HTML
  shells while its content-hashed `_next` bundles are gitignored and kept out of
  the release commits, and `_dashboard_dir()` accepted the first directory with
  an `index.html` — so it chose that partial export, `/` answered 200, and every
  chunk 404'd while React never hydrated. The packaged copy under
  `server/dashboard` was complete and never reached. `_dashboard_dir()` now
  requires `_next/static` before it trusts an export and falls through to the
  packaged copy; an export with an `index.html` but no bundles is still returned
  as a last resort, so an intentionally bundle-less directory stays servable.
  Covered by `tests/test_dashboard_dir.py`.

### Docs: one tool count, and a test that keeps it that way (#103)

- The README's documentation table said "All 69 tools" while the feature list
  four sections above said 73, and the landing page at `docs/index.html` still
  advertised 59. Three answers to one question, none of them checked.
  `README.md` and `docs/index.html` now say 73, and
  `test_no_published_prose_quotes_a_stale_tool_count` reads every count in the
  root `*.md`, the published `docs/*.md` and the landing page against
  `server.tools.profiles.profile_counts()`, so the next tool cannot leave one
  behind. `CHANGELOG.md` is excluded: each entry records the count that release
  actually shipped, and forcing those to today's number would falsify history.

### Contributing: require DCO sign-off (#103)

- `CONTRIBUTING.md` now asks for `git commit -s`, and the pull request template
  carries it as a checklist item. The project is AGPL-3.0-or-later; a
  contribution with no provenance cannot later be relicensed or offered under a
  commercial term without going back to every author, and the sign-off is what
  keeps that door open. It is not a copyright assignment.

### Frontend: an automated accessibility gate (#103)

- Accessibility coverage was manual and thin — 18 `aria-label` attributes
  across 10 files and 18 pages, with no automated check. `jest-axe` is wired into
  the vitest setup and `src/components/ui.a11y.test.tsx` runs axe over the UI
  primitives every page is built from: labelled form controls, a titled dialog,
  and a card. One test asserts axe *rejects* an unlabelled input, so a broken
  matcher registration cannot masquerade as a clean suite.

### Frontend: six comboboxes had no accessible name (#103)

- The primitive-level gate above never rendered a page, so it could not see the
  filters built on top of the primitives. `src/app/pages.a11y.test.tsx` renders
  each top-level page and runs axe over the result, and it immediately found
  three `button-name` (critical) violations on `/memories`. The cause is
  structural, not cosmetic: Radix `SelectTrigger` renders `role="combobox"`, and
  ARIA takes an author-provided name for that role — its inner text is not one,
  so the trigger announces as "combobox" with no label. `aria-label` added to all
  six triggers (`memories`: memory type, project, source; `projects`: context
  file format; `onboarding-empty-state`: client, tool profile). Verified against
  the real browser a11y tree, not just the component harness: axe reports 0
  violations across the top-level routes and the three comboboxes now expose
  "Memory type", "Project filter", "Source filter".

### Frontend: end-to-end tests over a real server and browser (#103)

- Every existing frontend test mocks `@/lib/api` and every backend test imports
  the app directly, so nothing exercised a page against the JSON the server
  actually returns — the seam where a renamed field or a moved route survives
  both suites. `frontend/e2e/` boots `levh serve` on two ports (one tokenless,
  one gated by `LEVH_TOKEN`) against throwaway SQLite databases and drives it
  with Playwright: the dashboard serves and hydrates, every top-level route
  renders without a client-side crash, a memory written from the UI appears in
  the list and in search, a memory written over MCP SSE appears in the
  dashboard, and the auth gate locks page content until the token is entered and
  rejects a wrong token at the API. `workers: 1` because the servers share a
  SQLite file. A new `e2e` CI job runs them after building the export, and
  uploads the Playwright report when it fails.

### Frontend: the E2E server helper rebuilds an incomplete export (#103)

- `frontend/out/` is committed as HTML shells, but its `_next/` bundles are
  gitignored and excluded from the release commits, so a fresh checkout has an
  `index.html` that points at hashes no process can serve. The helper's "build
  only if `index.html` is missing" check therefore left that partial export in
  place, and the server answered `/` with 200 while every chunk 404'd. It now
  requires `out/_next` to exist before trusting the export. The underlying
  mismatch between what the release commits and what `_dashboard_dir()` prefers
  is tracked separately.

### Frontend: the search shortcut is real now (#103)

- The header printed "⌘ K — Search memories" and the Quick Help dialog listed
  it alongside ⌘⇧A, but no listener existed for either key: the badge was
  decoration and the shortcut did nothing. A documented shortcut that is dead
  reads as a broken app rather than an unbound key. `CommandPalette` now backs
  the badge — a dialog over the same recall endpoint, with debounced search,
  arrow-key navigation, Enter to open the memory and a live-region result
  count. ⌘K toggles and ⌘⇧A reaches quick capture, but only outside a text
  field, where that combination is select-all and stealing it would break
  editing. The header's inline `<form>` was removed rather than kept as a
  second, divergent search surface. Covered by `palette.spec.ts` (browser) and
  `command-palette.a11y.test.tsx` (named dialog, labelled field).

## 2.32.0

### Frontend toolchain: Tailwind v4, ESLint 9, Next 16 (#188, #186, #189)

- Tailwind CSS upgraded to v4: PostCSS plugin moved to `@tailwindcss/postcss`,
  `@tailwind` directives replaced with `@import "tailwindcss"` + `@config` to
  preserve the existing shadcn-style TS theme; `autoprefixer` dropped (built-in).
- ESLint upgraded to v9 with `eslint-config-next` v16; legacy `.eslintrc.json`
  replaced by flat `eslint.config.mjs`. The one genuine `react-hooks/immutability`
  finding (agents page) was fixed; the 23 `set-state-in-effect` findings are
  disabled pending a dedicated refactor of the prop-to-state sync idiom.
- Next.js upgraded from 15.5 to 16.3.

### mcp constraint widened to <3 (#184)

- `mcp>=1.0,<2` broadened to `mcp>=1.0,<3` in `pyproject.toml`; `uv.lock`
  regenerated so `uv lock --check` passes. The locked mcp version is unchanged.

### Next 16 regressions fixed: lint gate and themeColor (#256, #257)

- `next lint` was removed in Next 16; `next build` no longer ran ESLint. Added
  an explicit `npm run lint` CI step and a `lint` script to `package.json`.
- `themeColor` moved from the `metadata` export to `viewport` as required by
  Next 16; `<meta name="theme-color">` is now present in the static output again.

### The API surface is versioned and frozen as a contract (#193)

- The same handlers are now served under `/api/v1/...`, and the *versioned*
  paths are the published contract: they are what `openapi.json` freezes and
  what CI checks for drift. The bare `/api/...` paths keep working as a
  compatibility alias but are hidden from the schema, so a client can at last
  tell a compatible edit from a breaking one. `/api/health` stays unversioned
  by design.

### The backend has a type gate again (#195)

- `mypy` now runs as a CI job over the tier of modules that is annotation-clean
  today (`[tool.mypy].files` in `pyproject.toml`). The list is a ratchet: widen
  it as modules are fixed. This restores `#147`'s deferred type-check item,
  which `#176` left without a follow-up.

### The generated API docs follow the token gate (#144)

- `/docs`, `/redoc` and `/openapi.json` were served without a token even when
  `LEVH_TOKEN` was set, because the gate only covered the `/api/` prefix — an
  authenticated deployment handed any anonymous caller a complete map of all
  108 routes. A browser cannot attach `X-LEVH-Token` to the `/docs` document
  request, so the surface is now withheld (404) while a token is in force, and
  restored deliberately with `LEVH_ENABLE_API_DOCS=true` on a trusted network.

### The process exposes Prometheus metrics (#145)

- `GET /api/metrics` (and `/api/v1/metrics`) publishes the in-process registry
  in text exposition format: recall/store latency histograms, the embedder
  fallback and derived-rebuild counters, and the admission-verdict
  distribution. The Docker `HEALTHCHECK` scrapes it as well, so a process whose
  metrics surface is down no longer reads as healthy.
- Every request gets a `request_id` — reused from `X-Request-ID` when it is a
  sane `alnum`/`-_.:` string under 128 chars, generated otherwise, echoed in the
  response header — and it is injected into each log record, which is what lets
  the recall → admission → store lines be stitched back together. With
  `LEVH_LOG_JSON=1` the logs are emitted one JSON object per line instead of the
  human-readable format.
- `GET /api/readyz` separates readiness from liveness: it pings SQLite, reports
  the live embedder mode and whether derived state is behind, and answers 503
  with the reasons when it is not ready.

### The supply chain is pinned and audited (#146)

- `uv.lock` pins the full backend graph and CI installs from it with
  `uv sync --frozen`, so one commit no longer resolves to a different set of
  packages weeks later; `uv lock --check` fails the build on a stale lock. The
  audit follows the lock too: `pip-audit` runs against `uv export` output rather
  than whatever pip happened to resolve that day, and Dependabot keeps both the
  lock and the npm graph current.
- Releases now carry CycloneDX SBOMs for the Python and frontend artifacts, and
  a `sast` job runs Bandit at Medium+ so a new finding cannot land under the
  gate unnoticed.

### Hook installers no longer promise a checkpoint they cannot install (#124)

- `install_claude_code_hook` and `install_universal_hook` accepted a
  `with_checkpoint` argument and silently dropped it: nothing read the
  value, the CLI never defined `--with-checkpoint` so the path was
  unreachable, and `_CHECKPOINT_TEMPLATE` had no callers. The dead
  parameter and the unused template are gone, and the module docstring no
  longer advertises a periodic-checkpoint capability these hooks do not
  provide. Recurring checkpoints remain available via `levh checkpoint auto`.

### A row the model cannot read no longer takes the store down (#267, #273)

- A store row the model rejects — an outside agent writing
  `memory_type="long_term"`, or a NULL `id` that SQLite allows on a non-INTEGER
  primary key — used to abort every startup: `EpisodicMemory.get_all()` raised
  out of `MemoryEngine.initialize()` and every other memory became unreachable.
  Such rows are now quarantined: skipped with a warning that names the row, so
  the store stays readable.
- The store now enforces the model's own contract on INSERT and UPDATE through
  triggers generated from one rules table in the schema module (id, content,
  memory_type, importance, frequency, timestamps). A rejected write names the
  column and what the model needs, and the store file is left untouched.
- A failed startup no longer leaves the process alive forever: the engine is
  published before `initialize()` and shut down in every outcome, so
  `levh serve` exits non-zero on startup failure instead of hanging on the
  open aiosqlite worker thread.

### Quarantined rows are visible, and doctor counts them the way the read path does (#270, #272)

- Every quarantine increments `levh_memory_rows_quarantined_total` in the
  Prometheus registry, and `levh doctor` fails on any row the current model
  cannot accept — so a store quietly rotting under an external writer produces
  a standing signal instead of passing a health review silently.
- The doctor count now judges rows by meaning rather than storage shape:
  `metadata` and `tags` are NULL when never set, and reading those columns raw
  made every sparse row look invalid (2 genuinely unreachable rows read as 18).
  The storage-shape-to-model conversion lives in one place —
  `server.core.db.memories.row_to_memory_dict` — shared by the query layer and
  the doctor check, with an equivalence test pinning the two together.
- `levh doctor` inspects SQLite from a running event loop, so the check no
  longer trips on the async driver it is reading through (#271).

### Structured startup and static-export polish (#284, #268)

- `levh serve` printed its startup banner with bare `print()`, so
  `LEVH_LOG_JSON=1` did not make it structured even though `logging.py`
  advertised that path. It now routes through `emit()` — verbatim when JSON is
  off, a JSON record when on — and the stale docstrings describe what `emit()`
  is actually for (#284).
- The static-export logo opts out of `no-img-element` explicitly, so the
  frontend lint gate stays green without a blanket rule change (#268).

### Dependency and CI maintenance

- Dependabot bumps across the frontend graph (vitest 2 → 5 with a vite 6.4
  floor, jsdom 25 → 30, recharts 2 → 3, `@types/node` 20 → 26, lucide-react,
  `@testing-library/jest-dom`) and the workflow graph (`actions/setup-node`,
  `actions/setup-python`, `actions/upload-pages-artifact`,
  `astral-sh/setup-uv`), plus dependabot rules that stop it opening major
  TypeScript and ESLint bumps that would need a migration of their own.

## 2.31.0

### Now installable, offline-first (PWA) (#86)

- LEVH can be *installed as a standalone app*: `manifest.webmanifest` (name,
  theme color, maskable 192/512 icons), an offline-first service worker
  (`sw.js`, static assets cache-first, shell network-first with an offline
  fallback, API/MCP passthrough) and deferred registration
  (`pwa-register.js`).

### Findings inbox — LEVH reports what it noticed, a human decides (#83)

- A new inbox where internally-noticed items surface instead of disappearing.
  The dashboard lists them under **Findings**; each can be accepted
  (`/api/findings/{id}/decide`) or left. Nothing is auto-applied or deleted.

### A safer, quieter librarian (#84)

- Removed the librarian's shell authority and resolved the LLM endpoint once
  at startup instead of per unit of work.
- Provider quota refusals are now explained (model/supplier 429) instead of
  hidden, and active agents are no longer mislabelled as silent.
- Watchdog/event-loop robustness fixes.

### Continuity briefs for every agent (#81)

- Auto-brief and non-repeating auto-summary for all agents; a
  `get_continuity_brief` MCP tool with auto-inject, and the brief now leads
  with the last checkpoint.

### The embedder tells you when it degrades

- `GET /api/config` now reports `requested_embedder_mode` alongside the
  effective `embedder_mode`, plus `embedder_fallback_reason` when an
  `auto` → hash fallback happens, so degraded scoring is never silent.

### Build & reliability

- Pinned `mcp<2` (the 2.x SDK renamed `FastMCP` to `MCPServer`), fixing CI.
- Anchored the frontend workspace root (`outputFileTracingRoot`) so a stray
  `package-lock.json` in a user's home can no longer break local `@/*`
  alias resolution.
- Docs/README hygiene: removed roadmap, demo assets, and stale references.

## 2.30.0

### Universal Agent Tracking System

- **Track any AI agent.** `server/core/agent_tracker.py` monitors agent activity
  across sessions — heartbeats, tool calls, memory usage — without requiring
  agent-specific integrations.
- **Auto-heartbeat.** Agents automatically send periodic heartbeats via
  `agent_heartbeat.py`, so the server always knows which agents are active.
- **Agent Activity dashboard.** New `/agents/` page shows real-time agent status,
  recent activity, and performance metrics.
- **Agent collaboration tools.** `server/tools/agent_tracking.py` provides MCP
  tools for agents to discover and coordinate with each other.
- **Auto-connect hooks.** `server/commands/universal_hooks.py` generates
  agent-specific configs that auto-connect to LEVH on startup and inject a
  continuity brief with recent sessions, pinned memories, and guard rules.

### Frontend UX Redesign

- **Memory Quick Add FAB.** Floating action button with template picker for
  decisions, conventions, context, and insights. Modal-based form with tag
  chips, project selector, and importance slider.
- **Improved Memories page.** Prominent search bar, clickable tag cloud,
  color-coded type indicators, skeleton loading, and better empty states.
- **Visual Connectors.** Card grid grouped by category (Files, Productivity,
  Notes, Development) with icons, descriptions, and sync status.
- **Client selector cards.** Visual cards for Claude Desktop, Claude Code,
  Cursor, Windsurf, and VS Code (Cline) with copy-to-clipboard.
- **New CSS components.** FAB, template, tag, skeleton, memory, connector,
  and client card styles with full dark mode and responsive support.

### The admission gate stops losing what it will not decide

- **A `review` verdict now holds the candidate instead of dropping it.** The
  gate has four verdicts; three had a destination and `review` did not. The
  caller was told the memory was not stored, and the content was gone — while
  the docstring said "hold for a human". `reject` and `review` are not the same
  refusal: `reject` is the gate deciding (too short, or a near-exact duplicate
  of something already remembered), `review` is the gate *declining* to decide,
  which is exactly the case where the difference may be the part worth keeping.
  Held candidates live in a new `held_memories` table, readable at
  `GET /api/memories/held` and settled with
  `POST /api/memories/held/{id}/admit` or `.../discard`. A held row is not a
  memory: no embedding, no decay, and it never answers a recall. Admitting one
  reproduces the memory you originally asked for — importance, tags, session,
  project, source and type all survive the detour. Secrets are redacted before
  holding, because a queue a person reads later is no place for a live
  credential.

### Backups carry the files they promised

- **A portable backup now contains the bytes of every attachment LEVH owns.**
  Attachment rows travelled as a path, a hash and a size; restoring on another
  machine wrote the same absolute path back, so the record looked restored while
  the file it named was not there. For a file uploaded through LEVH that is data
  loss — no other copy exists. A file you attached from your own disk still
  travels as a reference: your original is the copy that matters, and a backup
  of your *memories* has no business collecting the documents around them. The
  envelope reports both counts, so the gap is stated rather than discovered on
  the day you restore, and carried bytes are checked against the recorded
  sha256 before they are written.
- **A re-broken attachment shows up as open again.** An attachment candidate's
  id is derived from the attachment, so once one had been resolved a second
  break re-used that id, the insert was refused, and the row stayed `resolved` —
  a broken file with nothing open against it. Verification's own `resolved` is
  now reopened; an open candidate is refreshed so a file that went from changed
  to missing says so; and a verdict *you* gave stands for the state you judged,
  reopening only when the signal itself is different.

### Sessions can be deleted

- **`DELETE /api/sessions/{id}`**, with the memory policy stated rather than
  assumed. `memories=refuse` (the default) deletes only an empty session and
  answers 409 with the count otherwise — tidying up a session is not a decision
  to delete the memories it produced. `detach` keeps them and drops the session
  link; `delete` removes them through the same cascade a single delete uses.
  Removing a session used to mean opening `stackmemory.db` and running SQL by
  hand, which is a write path around the product's own API.

### Fixes

- Onboarding writes its receipt where it can actually write. The previous check
  proved the directory could be *created*, and `mkdir(exist_ok=True)` succeeds
  as a no-op against a `.stackmemory` that already exists and is read-only — so
  the fallback never ran and the write failed instead. Writability is now proven
  by writing, and a write that fails anyway falls back at that point too. A path
  you named explicitly still fails loudly rather than being relocated behind
  your back.
- The onboarding status endpoint counts demo memories in SQL. It was loading
  every memory — content, embeddings and all — to produce one number, so the
  cost of showing a demo badge grew with the corpus it was reporting on.
- Tests read documents as UTF-8 instead of the console code page, and two tests
  no longer fail purely because the suite is busy.

### Sessions and continuity

- **Sessions start with your memory already in them.** `levh hook install
  --client claude-code` registers a Claude Code SessionStart hook, so a new
  session opens with the rules you recorded, the memories you pinned and where
  you left off — without anyone asking for them. The hook prints nothing when
  there is nothing to say and exits 0 even when LEVH is broken; a memory tool
  that stops you starting work is worse than none.
- The continuity brief now carries pinned memories and mistake-guard rules.
  Everything else in it is a keyword read of recent activity; these two come
  from the pin flag, because they are what the user explicitly said never to
  forget and must not depend on a phrase matching a list.
- `levh continue --if-any` prints nothing when there is no activity, instead
  of an empty frame.

## 2.29.0

### Mistake guard

- **A corrected mistake becomes a rule that outlives the session.**
  `record_mistake` stores it as a pinned memory — pinned memories are exempt
  from H(x,ψ) decay — plus a row in the new `violations` table recording the
  incident that taught it. Rules lead the generated context file under
  "Rules Learned From Mistakes", so the next session reads them before
  working. `list_mistakes` reads the log back.
- **Readable by a person, not only an agent.** `GET /api/guard/rules`,
  `GET /api/guard/violations`, `POST /api/guard/mistakes`, and a "Mistake
  guard" page in the dashboard under Intelligence.
- Deliberately not included: deciding whether a *proposed* action violates a
  rule. That runs in front of every tool call and needs a latency budget and
  a false-positive story that recorded rules can inform but this layer cannot
  assume.

### Connectors and clients

- `local_files` no longer chunks by default. Each file becomes one memory
  regardless of size; pass `chunk_size` in the connector config to opt back
  into splitting large files (with `overlap`, default 200).
- **Upload calendar, mailbox and transcript files from the dashboard.**
  Importing used to require typing an absolute filesystem path while the JSON
  import on the same page used a file picker. Browsers expose a file's
  contents but never its path, so the bytes are uploaded and the server
  returns the path to import from. The stored file is named from an
  identifier, never from the request.
- **Five more MCP clients:** jcode, omp (oh-my-pi), opencode, Codex and
  Hermes. Three of them never read the JSON shape LEVH emitted — opencode
  uses a different schema, Codex uses TOML, Hermes uses YAML — so config
  generated the old way was silently ignored. Output is now format-aware and
  verified with each format's own parser.

### Scaffolding

- **`levh mcp init <name> --with-memory`** writes a working FastMCP server
  that imports the installed package and shares this database, so a memory
  stored through it appears in the dashboard too. `--deploy fly|railway|
  render|docker` adds a deploy config; all four point `SQLITE_DB_PATH` at a
  persistent volume, because an ephemeral filesystem would start every
  restart with an empty memory.

### Fixes

- **Public demo mode kept its search.** `recall` POSTs because it carries a
  query, so the blanket "block every mutating method" rule left the demo
  without memory search while the WebSocket path allowed the same action.
  Recall is allowed again with reinforcement forced off, so an anonymous
  search cannot reshape a shared store. 19 tests now lock the boundary that
  previously had none.
- **`levh sync` no longer echoes credentials.** Connector config carries
  tokens and API keys, and an exception message is written by the connector
  or its HTTP client — which put the URL, credentials and all, into the
  error that was printed verbatim. Supplied values are stripped from the
  message. A malformed `--config` pair is reported without repeating it.
- **API errors say what went wrong** instead of rendering `[object Object]`.
- **The dashboard's popovers are opaque again** — a second Tailwind config
  was shadowing the first and dropping the theme tokens.
- **`python -m server.cli --version`** derives from package metadata rather
  than a second hard-coded semver.
- `audit_secrets` returns `secret_types`, named after the detector labels it
  actually holds; it never returned the credential it matched.

### Internal

- The five largest files were split by responsibility, with public surfaces
  unchanged and verified: `memory_engine.py` 2829 → 290, `cli.py` 1920 → 273,
  `api.py` 1782 → 237, `database.py` 1242 → 274, and the settings page
  1226 → 53. Route table, engine methods, database methods and CLI
  subcommands all diff clean before and after.
- `LEVH_TOKEN` and `LEVH_PUBLIC_DEMO` are read at call time instead of being
  frozen at import, so two modules can no longer disagree about whether
  writes are allowed.

## 2.28.0

### Launch remediation: all four Gate 0A P0 findings closed

- **Remote access requires a token.** Standalone MCP SSE now enforces
  `LEVH_TOKEN` like every other transport; the REST API, dashboard and
  WebSocket reject tokenless non-loopback peers directly in the ASGI layer
  (bypassing the CLI no longer bypasses the check). `LEVH_ALLOW_REMOTE_WITHOUT_TOKEN`
  is the explicit, documented escape hatch for deployments (like Docker
  Compose) whose network boundary is enforced elsewhere.
- **Outbound LLM calls require explicit opt-in.** An ambient `OPENAI_API_KEY`
  no longer activates Ask, session summaries, consolidation, or transcript
  ingest on its own — set `ANSWER_MODE=llm` / `SUMMARY_MODE=llm` per feature.
  `GET /api/config` reports the effective posture.
- **Content updates go through the admission gate.** `update_memory` used to
  embed and persist new content with no redaction and no minimum-length
  check; secrets typed into an edit are now redacted exactly as they are on
  create, before the text ever reaches the embedder.
- **Live peers observe writes without a restart.** Two processes (or engine
  instances) sharing one SQLite file — the real deployment shape, e.g. Claude
  Desktop plus the dashboard — now see each other's create/update/delete
  immediately. `recall()` checks SQLite's own `PRAGMA data_version` and
  refreshes its cache only when a peer actually wrote.
- Fixed MCP `serverInfo.version` reporting the `mcp` SDK's version instead of
  LEVH's own, and MCP tool output leaking raw enum reprs (`MemoryType.EPISODIC`
  instead of `episodic`) into recall/search/list/store/session text.
- Added `levh tune`: an offline, cross-validated fitter for the H(x,ψ)
  scoring weights. Reports what a fitted weight set is actually worth on a
  labelled query set and recommends keeping the shipped defaults when the
  gain doesn't survive leave-one-group-out validation. Changes no runtime
  behavior on its own.
- Restructured the README into a landing page; moved full reference material
  (MCP tools, REST API, CLI, connectors, configuration) into `docs/`.

Full suite: 596 passed, 0 xfailed.

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
