# LEVH LLM Wiki — Implementation Plan

**Status:** Active implementation plan; core feature and repository contracts are in the review branch, but release acceptance is not complete.
**Base:** origin/main in the isolated feat/llm-wiki-foundation worktree.
**Last reviewed:** 2026-10-27.

## 1. Goal

Build a local-first, source-linked Markdown Wiki over knowledge already captured
by LEVH. People and agents should be able to navigate topics, projects, people,
organizations, decisions and source evidence through durable pages, while LEVH's
existing SQLite memory store remains the authoritative source.

The Wiki is a **derived, rebuildable projection**, not a second memory database,
not an automatic upstream crawler, and not a claim that every captured fact is
true. Every generated evidence page must make it possible to trace statements
back to the LEVH memory records from which they came.

## 2. Product and engineering constraints

1. **Local by default.** A normal build makes no network calls to an LLM and
   does not require API credentials.
2. **Evidence before synthesis.** Deterministic source snapshots, metadata,
   page links and content hashes are the minimum product. LLM summaries are
   optional drafts layered over evidence, never replacements for it.
3. **Source identity and lineage.** Preserve LEVH memory IDs, recorded time,
   source, project, and safe source identity/revision metadata when present.
   Distinguish a source's provenance from the truth of what it says.
4. **Reproducible generation.** Stable IDs and ordering, atomic file writes,
   unchanged-file detection, a machine-readable manifest and a read-only lint
   operation must support repeat builds and diagnose drift.
5. **Deletion semantics.** If a source memory is explicitly forgotten or deleted
   from LEVH, the next successful Wiki build must remove its generated mirror.
   The Wiki must not quietly become an undeclared archive of deleted data.
6. **Secrets and untrusted input.** Run the existing LEVH secret redactor;
   remove URL query strings/fragments from supported URL metadata; treat source
   content as data, never as instructions. Sanitize generated labels and never
   trust LLM-returned Markdown, links or citations.
7. **Workspace separation.** A manifest belongs to one workspace. Do not allow
   a different workspace to read or build over the same manifest/root.
8. **No silent evidence repair.** A digest-addressed raw snapshot is immutable.
   If its path exists with unexpected bytes, lint reports the mismatch and a
   subsequent build refuses to overwrite the file.
9. **MCP permissions.** Build is a filesystem mutation and belongs in the
   admin profile. Search, page-read and lint tools may be available to
   read_only; they must not rewrite files or rebuild derived state.
10. **Bounded work.** Set explicit bounds for page search results, imported
    source batch sizes, external summary source text and summaries generated
    per build. A large knowledge base must not create unbounded requests.

## 3. Initial architecture and file contract

Default root: a llm-wiki/<workspace-slug>-<workspace-hash> directory beside
the configured persistent SQLite database. A caller may inject a root in tests.
An in-memory database is not a supported default because it has no stable
data directory.

Expected layout:

- raw/<memory-key>/<snapshot-digest>.md — sanitized snapshot of one LEVH memory version.
- wiki/index.md — entry point with project/entity navigation.
- wiki/sources.md — catalog of all current memory records, including records
  from which no typed entity is extracted.
- wiki/entities/<type>/*.md — entity evidence, related-entity links and optional
  review-required LLM summary drafts.
- wiki/projects/*.md — project evidence and entity hubs.
- wiki/log.md — append-only build history.
- manifest.json — source-to-snapshot mappings, page IDs/paths, digests and
  optional LLM draft cache; written last after page generation.
- AGENTS.md and schema.md — local authoring rules and output schema.

Build sequence: authorize → validate the existing manifest/workspace → read
memories in bounded batches → sanitize and snapshot source versions → derive
entity/project evidence pages → optionally create cited LLM drafts after
explicit opt-in → write source catalog and index → append build log → write
manifest last. Lint is a separate read-only path and never repairs a failed
digest or broken link.

## 4. Work already implemented in the current worktree

The following implementation is present on `feat/llm-wiki-foundation`; local test evidence is recorded below. The branch is not merged or deployed:

- Core Wiki engine in server/core/llm_wiki.py.
- REST routes in server/routes/wiki.py, response models in
  server/routes/models.py and router registration in server/api.py.
- MCP tools in server/tools/wiki.py, registry wiring in server/tools/register.py,
  and profile classification in server/tools/profiles.py.
- Initial automated tests in tests/test_llm_wiki.py.
- User-facing guide in docs/llm-wiki.md and an Unreleased CHANGELOG entry.
- OpenAPI export updated to pin imports to the current repository root, and
  openapi.json regenerated from this worktree.

Verified on this worktree at the time of writing:

- Python compile check: passed.
- Ruff on touched Python files: passed.
- Wiki tests: 7 passed, including REST integration, secret redaction,
  idempotency, explicit LLM opt-in, workspace separation and snapshot tampering.
- MCP profile boundary tests: 11 passed.
- OpenAPI contract tests: 8 passed.
- OpenAPI export --check: passed.
- Documentation consistency tests: 34 passed. REST endpoint and MCP tool tables,
  profile-band counts, and published tool-count prose have been brought in sync.
  Current code counts are read_only=39, minimal=6, work=32, admin=73, full=78.
- Combined targeted verification: 60 tests passed across Wiki, MCP profile
  boundaries, OpenAPI contract and docs consistency suites.

These are local test results only; no CI run, pull request, merge or production
deployment has occurred. The feat/llm-wiki-foundation branch is based on
origin/main and is intended for agent/reviewer inspection.

## 5. Remaining implementation plan

### Phase A — Finish repository contracts and operator documentation

Status: **Complete; local acceptance passed.**

- Added all five versioned Wiki routes to docs/api-reference.md.
- Added all four MCP tools to docs/mcp-tools.md; build is classified as an
  admin-tier mutation, while read/search/lint have their intended read-only scope.
- Updated profile-band prose to minimal=6, work=32, admin=73, full=78, with
  read_only=39 documented separately from the cumulative size ladder.
- Updated published tool-count references and kept the Wiki guide aligned.
- Re-ran tests/test_docs_match_code.py: 34 passed.

Acceptance: satisfied locally; no assertion was weakened or skipped.

### Phase B — Harden source integrity and filesystem behavior

Status: **Complete; local acceptance passed.** Manifest structure is validated
before use, builds for one root serialize, and path/symlink/traversal/delete
cases have regression coverage in tests/test_llm_wiki_hardening.py.

- Test that manifest paths, snapshot paths, page paths and Markdown links
  cannot escape the Wiki root through traversal, absolute paths or symlinks.
- Verify path checks hold for existing symlinks and ensure no build follows an
  unexpected symlink into arbitrary user files.
- Keep raw snapshot paths immutable and digest-addressed. A modified file must
  remain available for inspection after a later build attempt refuses it.
- Add tests for corrupt/unknown manifest versions, missing files, malformed
  manifest structures, snapshot deletion and stale generated pages.
- Make concurrent builds for one root serialize or fail clearly; they must not
  race page writes, log appends or the final manifest update.
- Ensure a failed build leaves the last valid manifest intact and is safely
  rerunnable. Clarify that some newly written orphan files may need lint/rebuild
  handling if failure occurs before the manifest commit point.
- Validate and escape metadata-derived project names/entity labels for both
  Markdown rendering and generated file paths.

Acceptance: targeted path/manifest/concurrency tests pass; tampering remains
visible; there are no file writes outside the configured Wiki root.

### Phase C — Verify LEVH data semantics

Status: **Complete; local acceptance passed.** Update/forget/no-entity and
deterministic-byte cases have regression coverage in tests/test_llm_wiki_hardening.py.

- Confirm source snapshots use the current LEVH memory contract and include
  superseded records only when the store API explicitly returns them.
- Prove that updating one memory creates a new snapshot while retaining the
  prior snapshot for as long as that memory remains in LEVH.
- Prove that an explicitly forgotten memory is removed from the manifest, raw
  snapshots, source catalog and generated pages after a successful rebuild.
- Preserve source identity/revision metadata when present; do not claim exact
  upstream file fidelity when LEVH only stores an excerpt or transformed text.
- Ensure records with no typed entities are still accessible from wiki/sources.md.
- Ensure every entity/project evidence row links to the correct snapshot and
  every manifest source points to that memory's current snapshot.
- Verify identical inputs produce deterministic page bytes. The build log and
  manifest timestamp may change per run; generated evidence pages should not.

Acceptance: update/delete/no-entity cases have regression tests and every
manifest mapping passes lint.

### Phase D — Validate REST and MCP contract boundaries

Status: **Complete; local acceptance passed.** Read paths are asserted not to
mutate memories or Wiki files, legacy/versioned routes agree, viewer principals
cannot build, and read_only cannot call build by hidden name.

- Keep REST routes under the versioned /api/v1/wiki surface and verify
  legacy routing/version cloning behavior, authentication and public-demo write
  restrictions via existing contract middleware.
- Verify status/search/page/lint are read-only and do not change SQLite memories,
  derived state, the manifest or Markdown files.
- Verify build is rejected for viewer principals and is allowed only when the
  current workspace authorization permits filesystem-store action.
- Verify the MCP read_only profile can search/read/lint but cannot call
  build_knowledge_wiki even by hidden tool name.
- Keep the profile table/classification tests strict: every registered tool
  must be classified and the read_only allowlist must remain disjoint from
  mutating tools.
- Verify invalid search inputs/limits and missing page IDs return stable,
  documented error shapes.

Acceptance: route authorization and MCP profile tests pass; no read path mutates
Wiki files or memory state.

### Phase E — Validate optional LLM summaries safely

Status: **Complete; local acceptance passed.** Fully mocked provider tests cover
opt-in, zero-call default, caching, invalidation, sanitization and failure
isolation; no live API key is used.

- Keep summaries disabled unless both the existing ANSWER_MODE=llm policy and
  explicit llm_summaries=true build input are present.
- Never include API keys in prompts, outputs, errors or logs. Redact source
  text and allowlisted metadata before outbound transfer; make the transfer
  boundary clear in REST/MCP docs.
- Mock the provider and test accepted/denied modes, request timeout, HTTP error,
  malformed response, empty output, oversized output and uncited output.
- Verify every citation maps only to numbered evidence provided to that
  particular summary, not an unrelated memory or a page-wide index.
- Sanitize LLM output; reject raw HTML, file/system paths outside the Wiki,
  unsupported links, credential strings and malformed citations. Treat source
  text as adversarial prompt-injection input.
- Cache summaries only against ordered snapshot IDs, source ordering and prompt
  version. Source changes invalidate the corresponding draft; unchanged sources
  should not trigger duplicate provider calls.
- Keep a per-build budget and explicit counters for generated, failed and
  skipped drafts. A failed summary must not block evidence generation.

Acceptance: offline tests prove zero HTTP requests by default; mocked remote tests
prove opt-in, bounded requests and citation filtering with no live API key.

### Phase F — End-to-end and CI gate

- Run the new Wiki test module, MCP profile boundary tests, OpenAPI contract,
  docs consistency checks, relevant engine/entity tests, and the project's
  full required CI-equivalent gate.
- Run ruff and git diff --check; inspect OpenAPI only after source routes/models
  stabilize, then run export_openapi.py --check.
- Inspect the final diff for unrelated edits and verify only the intended
  worktree changed.
- Review test output for warnings/failures instead of relying on process exit
  alone. Add tests for every defect found during this phase.
- Update this plan's status and the user guide only after the acceptance
  criteria pass.

Acceptance: all required local gates pass, OpenAPI is deterministic, no
unrelated changes are present, and CI evidence is available before proposing
a pull request.

## 6. Explicit non-goals for this first delivery

- Do not fetch or crawl all upstream connector content directly from Wiki code.
  The existing LEVH connector sync remains responsible for ingest/update; the
  Wiki rebuilds from LEVH's stored state.
- Do not make an LLM the authority for facts, truth scoring or source identity.
- Do not silently rewrite memory content, update trust scores, reindex the graph,
  delete memories, or mutate database state during Wiki reads.
- Do not promise perfect redaction, archival fidelity or compliance certification.
  The sanitized Markdown output must be reviewed before sharing externally.
- Do not add a web UI, vector search over Wiki files or an independent scheduler
  until the source, lineage, privacy and validation contracts are stable.

## 7. Execution order and release gate

1. Phase A: **complete**; docs consistency is green (34 passed).
2. Phase B: close filesystem, manifest and concurrency edge cases.
3. Phase C: verify source update and deletion semantics.
4. Phase D: prove REST/MCP authorization and no-write read paths.
5. Phase E: add fully mocked LLM boundary tests and adversarial-output checks.
6. Phase F: run the complete quality gate, inspect the final diff and prepare
   review evidence.

Do not mark the feature complete or create a release/PR until every phase's
acceptance criteria has evidence. Any deferred criterion must be named in the
PR description, not hidden behind a passing happy-path test.
