# LLM Wiki

LEVH's LLM Wiki is a local, source-linked Markdown projection of the memories
already held in LEVH. The SQLite memory store remains authoritative. The Wiki is
meant to make captured knowledge easier for people and agents to browse, cite,
and check; a generated summary is never stronger evidence than its linked source
snapshots.

## First build

Use the same LEVH environment and persistent database as the MCP/API server.

- REST: **POST /api/v1/wiki/build** with an empty JSON object.
- MCP: **build_knowledge_wiki** with its default arguments.
- Check the result: **GET /api/v1/wiki/lint** or **lint_knowledge_wiki**.

The default root is under the directory that contains the configured SQLite
database: **llm-wiki/<workspace-slug>-<workspace-hash>/**.

Use **GET /api/v1/wiki/status** to inspect the last build. A build never
changes the memory rows in SQLite. The root can be browsed with any Markdown
viewer, and **wiki/index.md** is the entry point.

## Browsing in the dashboard

The packaged dashboard has a **/wiki** page (sidebar: *Knowledge Wiki*) so the
projection is reachable without the CLI, REST or MCP. It is read-only: it shows
the build status (memories, snapshots, pages, workspace root, LLM-summary
state), searches generated pages, opens one page's Markdown, and lists lint
issues. Building stays an admin-tier action and is deliberately **not** exposed
in the UI, so a viewer principal cannot write Wiki files from the browser. The
page reads through the same routes below; the build itself still runs over REST
(**POST /api/v1/wiki/build**) or MCP (**build_knowledge_wiki**).

## Output layout

- **raw/<memory-key>/<snapshot-digest>.md**: immutable snapshots of current and
  historical LEVH memory records. They retain the LEVH memory ID, timestamp,
  source, project and a carefully selected set of source metadata.
- **wiki/index.md**: a starting point linking to project hubs, entity pages,
  the source catalog, the conflict-candidate catalog, the build log and writing
  rules.
- **wiki/sources.md**: a complete catalog linking stored memories to snapshots,
  including records from which no typed entity was extracted.
- **wiki/entities/**: evidence pages for people, organizations, documents,
  tasks and other entity types extracted by LEVH. A page whose entity appears in
  an open conflict candidate also carries a **Conflict candidates** section.
- **wiki/projects/**: project hubs linking source evidence to entities.
- **wiki/conflicts.md**: the catalog of open conflict candidates LEVH already
  flagged, each linking to the two raw snapshots it compares. The Wiki does not
  detect conflicts and does not decide which memory is true.
- **wiki/log.md**: append-only build log. Each entry starts with a
  `## [<timestamp>] build | ...` header and keeps its counters on one
  `- build: ...` line, so plain tools like
  `grep '^## \[' wiki/log.md | tail -5` read the history.
- **manifest.json**: source/page IDs, snapshot paths, hashes, the conflict
  candidates shown this build, and the optional LLM draft cache.
- **AGENTS.md** and **schema.md**: local Wiki writing rules and output contract.

The manifest is written last so an interrupted build can be rerun. Generated
pages are written atomically. Repeated builds do not rewrite unchanged snapshots
and pages. Source updates create new snapshots; older snapshots remain while
their source memory still exists. When a memory is explicitly forgotten or
deleted from LEVH, the next build removes its mirrored snapshots and generated
pages. This preserves LEVH's deletion semantics; it is not an independent
long-term archive of data the user removed from LEVH.

## Evidence and privacy contract

Every generated entity/project page has a numbered Evidence section and links
to the corresponding raw snapshots. Entity connections are explicit co-occurrence
signals, not proof that a relationship is true. LEVH's source/trust metadata
describes provenance and reliability signals, not factual truth.

The Wiki snapshots are **normalized and credential-redacted records, not byte-
identical copies of upstream files**. The build runs LEVH's existing secret
redactor, removes URL query strings/fragments from supported URL metadata, and
keeps an allowlisted metadata subset. No redactor is perfect; review the outputs
before sharing a Wiki outside the machine. The default build is deterministic
and does not call a remote LLM.

The initial LLM Wiki is a generated view of existing memories. It does not
automatically sync upstream connectors itself: run the normal connector sync,
then build the Wiki again. LEVH records whose connectors provide stable
source_identity and revision metadata have better update lineage than items
without those identifiers.

## Optional LLM drafts

LLM-generated entity summaries are off by default. To request them, the local
operator must configure both:

- **ANSWER_MODE=llm**
- **OPENAI_API_KEY**

Then explicitly call **POST /api/v1/wiki/build** with
**{"llm_summaries": true}**, or pass **llm_summaries=true** to the MCP tool.
This uses the same explicit outbound-LLM policy as LEVH's existing answer
feature. It can send redacted source text to the configured OpenAI endpoint.
Each draft is marked as requiring review, capped to twelve new summaries per
build, limited to eight source memories per summary, and cached against the
ordered source snapshot IDs and prompt version. Drafts without valid numeric
citations are discarded. A failed summary does not block the deterministic
evidence pages. LLM output can still be wrong; check every claim against its
linked sources.

## Query and lint

- **GET /api/v1/wiki/search?q=...** (MCP: **search_knowledge_wiki**) searches generated Markdown
  using literal text and returns page IDs, paths and snippets.
- **GET /api/v1/wiki/pages/{page_id}** (MCP: **get_knowledge_wiki_page**) reads one
  manifest-indexed page.
- **GET /api/v1/wiki/lint** (MCP: **lint_knowledge_wiki**) verifies source snapshot
  SHA-256 digests, source/page manifest mappings, generated page hashes, required
  evidence sections and internal Markdown links. It also reports semantic
  problems: a registered page no index link reaches (**unreachable_page**) and a
  conflict candidate that points at a memory no longer indexed
  (**conflict_source_missing**). Lint is read-only and does not repair files;
  use a new build after reviewing a failure.

REST endpoints are also available through LEVH's versioned /api/v1 surface.
The corresponding MCP page-read/search/lint tools are available to read_only
profiles; building the Wiki is an admin-tier mutation because it writes files.

## What this is, and what it is not

The Wiki is a **derived, rebuildable projection** of LEVH's stored memory. Every
build regenerates the pages from LEVH's current state, so the Wiki reflects
updates, deletions and new conflict candidates on the next build rather than
drifting as a hand-edited document. The compounding value is deliberate and
bounded:

- Evidence, snapshot hashes and links are deterministic and regenerated every
  build; unchanged inputs do not rewrite bytes.
- Conflict candidates that LEVH already flagged are pulled in and cited next to
  the snapshots they compare.
- LLM summaries are optional, review-marked drafts layered over the evidence,
  never a replacement for it.

The Wiki is not an automatic crawler (run the normal connector sync first), not a
second memory database (build and read paths never change LEVH rows), and not an
authority on truth (conflicts are candidates; summaries require review).
