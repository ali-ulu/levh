<p align="center">
  <strong>LEVH</strong><br>
  Local-first memory layer for AI agents and humans<br>
  <em>Memory that forgets like you do — unless it matters.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/levh/"><img src="https://img.shields.io/pypi/v/levh?logo=pypi&logoColor=white" alt="PyPI version"></a>
  <img src="https://img.shields.io/badge/MCP-Protocol-blue?logo=anthropic" alt="MCP">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-AGPL--3.0--or--later-green" alt="License">
  <a href="https://levh.ai-ulu.com/"><img src="https://img.shields.io/badge/website-levh.ai--ulu.com-0D1117?logo=googlechrome&logoColor=white" alt="LEVH website"></a>
</p>

<p align="center">
  <a href="https://www.producthunt.com/products/levh?embed=true&utm_source=badge-featured&utm_medium=badge&utm_campaign=badge-levh" target="_blank"><img src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1199355&theme=light" alt="Levh - Local-first memory for AI agents and workflows | Product Hunt" style="width: 250px; height: 54px;" width="250" height="54" /></a>
</p>

<p align="center">
  <a href="docs/demo/5-minute-demo.md"><img src="docs/assets/levh-demo-tour.gif" alt="LEVH demo: local memory dashboard, entity graph, and conflict review" width="960" /></a>
</p>

---

## What is LEVH?

Your AI tools are stateless. Every session starts from zero.

LEVH gives them a **persistent, searchable memory** that lives on your machine — plug it into Claude Desktop, Cursor, Claude Code, VS Code, or any MCP client and it remembers your decisions, your projects, and your people across sessions.

Most memory tools optimize for perfect recall: store everything, retrieve everything, let the noise pile up. **LEVH forgets on purpose.** Every memory has its own decay curve; unused memories fade; the ones you actually rely on get reinforced automatically. Signal rises to the top without manual curation.

Everything runs locally on SQLite. No accounts, no cloud, no external services.

---

## One memory, every agent on your machine

Most memory layers are built for one client. LEVH is not — it is one SQLite
file that any number of MCP-speaking agents read and write concurrently,
because they are separate processes talking to the same database, not
separate memories synced after the fact.

In daily use on one machine that means Claude Code, Claude Desktop, Cursor,
Windsurf, VS Code (Cline), opencode, jcode, Codex, Hermes, kilocode and
oh-my-cli all resolve the same query the same way — an agent never re-solves
a problem another agent already closed, and never asks you to repeat
something you already said to a different tool. `Cross-process coherence`
below is not a footnote; it is the property that makes this safe under
concurrent writers.

Two things make this workable instead of noisy at that scale:

- **The admission gate has a fourth verdict.** Store / reject / update are
  obvious. The fourth is *review*: a candidate close enough to something you
  already have that auto-storing risks a duplicate, but different enough that
  discarding risks losing the one detail that mattered. It used to be
  dropped silently. Now it is held — visible, admittable, discardable — so no
  agent's write is ever the one that vanishes.
- **Sessions start already briefed, not told to go look — where the client
  supports it.** `levh hook install --client claude-code` (and the same for
  Claude Desktop, Cursor, Windsurf, VS Code/Cline) wires a native
  session-start hook that pushes pinned rules, recent sessions and open
  blockers in front of the agent before the first prompt. Codex and Hermes
  get the same brief through their own hook systems (`hooks.SessionStart` in
  `config.toml`, a `pre_llm_call` shell hook in `~/.hermes/config.yaml`) —
  same `levh continue --limit 5 --if-any` call, different wiring per client.
  Clients without a documented hook surface (opencode, kilocode, jcode, pi,
  the standalone Cline CLI) still get full MCP tool access and an AGENTS.md
  rule to call `recall_memory` first — active, not passive, until one of
  them ships a hookable session-start event. A memory tool an agent has to
  be reminded to consult is a filing cabinet with extra steps; not being
  able to remind it automatically yet is a narrower problem than not having
  the memory at all.

This isn't the first shared-MCP-memory idea — mem0's OpenMemory does the same
loopback-server pattern for a smaller client set. What's specific to LEVH is
combining that breadth with the decay model above and the hold-don't-drop
gate: memory that stays *useful* across a dozen concurrently-running agents,
not just present.

---

## How memory works here

This is the core mechanic, not a footnote:

- **Every memory has its own half-life.** New memories start at 168h and fade fast unless something happens.
- **Recall reinforces.** Retrieving a memory resets its clock and grows its half-life — the same spaced-repetition effect Anki uses.
- **Importance accelerates it.** A `0.9`-importance memory consolidates far faster per recall than a `0.1` one.
- **Feedback closes the loop.** Mark a memory unhelpful and its stability drops, so stale information fades instead of resurfacing.
- **New information interferes with old.** "The deploy branch is prod" naturally supersedes "the deploy branch is main" — no one has to delete anything.
- **Pinning is permanent.** Rules and facts that must never be forgotten skip decay entirely.
- **Fading memories surface for review** — rescue what still matters with one click, let the rest go.

```
retention(t) = 0.5 ^ (hours_since_last_recall / stability_hours)
```

Memories are ranked by an explainable multi-factor score, `H(x,ψ)` — semantic similarity, decay, importance and access frequency, each weight configurable and every score breakable into its components in the UI. See [Architecture](docs/ARCHITECTURE.md).

---

## Quick Start

```bash
pip install levh
levh setup --demo --client claude --profile work
levh serve
```

Dashboard and API come up on <http://localhost:8000>. `--demo` loads a small
deterministic corpus — people, organizations, decisions, and one real conflict
candidate — so every view has something to show.

Starting with your own data instead:

```bash
pip install levh
levh setup --real --client claude --profile work
levh capture "Atlas uses PostgreSQL in production."
levh serve
```

Then open **Settings** in the dashboard for copy-paste MCP configs, or run
`levh mcp config cursor` for any supported client.

Optional, once you are set up:

```bash
levh hook install --client claude-code   # every new session starts with your memory
levh hook install               # capture every git commit message
levh context -o CLAUDE.md       # compile memory into a context file
levh mcp init my-server --with-memory   # scaffold an MCP server on this database
```

→ [Getting Started](docs/getting-started.md) · [5-minute demo](docs/demo/5-minute-demo.md) · [Installing from source](docs/installation.md)

---

## What you get

- **Adaptive decay** — per-memory half-life, reinforced by recall, weakened by negative feedback, visualized as a forgetting curve.
- **Ask your memory** — natural-language questions return an answer that cites the exact memories it drew from. Deterministic and offline by default.
- **People, organizations & timeline** — who you interact with and what happened when, extracted automatically from calendars, email and transcripts. No manual tagging.
- **Daily briefing & meeting prep** — today's events, open commitments detected from your own words, and who you're about to meet. Fully offline.
- **Decisions & conflicts** — decision statements pulled out of your memories, and a review signal when two memories appear to disagree. A signal, never a verdict; nothing is auto-deleted.
- **Admission gate** — every incoming memory is screened before storage, on create *and* on update: duplicates flagged, secrets like API keys redacted before they are ever embedded. Deterministic, offline. When the gate declines to decide — close to something you already have, but not identical — the candidate is **held for you** rather than dropped, to admit or discard later. A refusal the gate cannot justify never costs you the content.
- **Trust & provenance** — an explainable reliability score per memory from source type, corroboration and review history. Separate from ranking; not a truth claim.
- **Entity knowledge graph** — memories indexed into real entity tables, so "which memories mention X" is a join, not a search.
- **Remembers you without being asked** — a SessionStart hook puts your rules, your pinned facts and where you left off into every new Claude Code session automatically. A memory the assistant has to be told to consult is a filing cabinet, not a memory.
- **Mistake guard** — a corrected mistake becomes a pinned rule plus an incident record. Pinned memories never decay, so the rule is still there weeks later, in a different session, and it leads the generated context file where the next session reads it before working.
- **Encrypted backup & restore** — a full portable snapshot including decay state and the bytes of every attachment LEVH uploaded, so a restore on another machine produces readable files rather than dangling paths. Files you attached from your own disk stay references — your original is the copy that matters. Optionally encrypted with a passphrase (PBKDF2 + AES).
- **Consolidation & review** — aged clusters collapse into durable summaries; the fading queue becomes a keep / reinforce / forget flow.
- **72 MCP tools**, a REST API, a WebSocket feed, and a live Next.js dashboard served by the API itself — one process, one port.
- **4 embedding modes** — OpenAI, local `all-MiniLM-L6-v2`, Ollama (fully offline), or a deterministic hash fallback. The system always works.
- **Connectors** for Calendar, Email, transcripts, Notion, Obsidian, GitHub and local files — all routed through the admission gate. Calendar, mail and transcript files are uploaded from the dashboard; there is no filesystem path to type.
- **Scaffold your own MCP server** — `levh mcp init my-server --with-memory` writes a working server that shares this database, optionally with a deploy config for Fly, Railway, Render or Docker.

→ [Full MCP tool list](docs/mcp-tools.md) · [REST API](docs/api-reference.md) · [CLI](docs/cli.md) · [Connectors](docs/connectors.md)

---

## Documentation

| | |
|---|---|
| [Getting Started](docs/getting-started.md) | First run, demo vs. real data |
| [Platform Setup](docs/mcp-client-config.md) | Claude Desktop, Claude Code, Cursor, Windsurf, VS Code (Cline), jcode, omp, opencode, Codex, Hermes |
| [Configuration](docs/configuration.md) | Environment variables, precedence, Docker |
| [Architecture](docs/ARCHITECTURE.md) | Layers, engine, scoring internals |
| [MCP Tools](docs/mcp-tools.md) | All 69 tools and the profile bands |
| [REST API](docs/api-reference.md) | Every endpoint |
| [CLI](docs/cli.md) | Every command |
| [Evaluation](docs/memory-evaluation.md) | Recall benchmark, golden fixtures, dogfood |
| [Testing](docs/testing.md) | Running the suite |
| [Releasing](docs/releasing.md) | Version bump, tag, automated publish |

---

## Measuring recall quality

Recall quality is measured, not claimed:

```bash
levh benchmark     # hit@1 / hit@3 / hit@5 / MRR on a labelled query set
levh eval run      # golden-fixture run through the full pipeline
levh tune          # fit the H(x,ψ) weights and report what it's worth
```

`levh tune` searches for better `HSCORE_*` weights against a labelled set and
reports the gain **cross-validated** — weights are fitted on some query groups
and scored on a group they never saw. On the small built-in corpus the fitted
weights do not generalise, and the command says so and recommends keeping the
defaults rather than printing an overfitted result. It is offline analysis: it
changes no runtime behaviour and only prints values for you to adopt.

Please don't quote hit@k or MRR numbers from anywhere other than a real run on
your own corpus and embedder mode — the hash fallback is non-semantic and will
understate quality. → [Evaluation](docs/memory-evaluation.md)

---

## Security

LEVH is a **local, single-user tool** — no accounts, no multi-tenancy.

- **Tokenless means loopback-only.** Without `LEVH_TOKEN`, remote peers are rejected — by `levh serve`, by the MCP SSE server, and by the ASGI apps directly, so bypassing the CLI does not bypass the boundary. Docker Compose opts into bridge traffic explicitly, and only because it publishes `127.0.0.1:8000`.
- **Shared-secret token** (`LEVH_TOKEN`) gates `/api/*`, the WebSocket and the MCP SSE transport, with in-process rate limiting on failed attempts. Set it before widening any bind.
- **CORS defaults to localhost origins**, not `*` — otherwise any site open in your browser could read your entire memory store. CORS is not an authorization boundary.
- **Nothing leaves the machine without an explicit opt-in.** An `OPENAI_API_KEY` in your environment is treated as a credential, never as permission — Ask, session summaries, consolidation and transcript ingest all run their offline backends until you set `ANSWER_MODE=llm` or `SUMMARY_MODE=llm`. `GET /api/config` reports the effective posture.
- **Secrets are redacted** by the admission gate before storage — on every write path, including updates, and before the text reaches the embedder. `audit-secrets` / `redact-secrets` find and strip anything stored before the gate existed.

This is not per-user auth, and it is not a substitute for your own reverse proxy
if you expose the service beyond localhost.

**Cross-process coherence.** Two processes sharing one database (for example
Claude Desktop and the dashboard) see each other's writes without a restart —
`recall()` checks SQLite's own `PRAGMA data_version` before scoring and
refreshes its in-memory caches if a peer wrote since the last check. `GET
/api/memories/{id}` and list/search endpoints read straight from SQLite on
every call and were never affected.

Found a vulnerability? See [SECURITY.md](SECURITY.md).

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| MCP Server | Python `mcp` SDK + `FastMCP` |
| API | FastAPI + Uvicorn |
| Database | SQLite via `aiosqlite` (auto-migrating schema) |
| Embeddings | OpenAI / sentence-transformers / Ollama / hash |
| Vector Search | NumPy cosine similarity |
| Frontend | Next.js 15 + React 19 (static export) + shadcn/ui + Recharts |
| Container | Docker (single image: API + dashboard) |

---

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)
and [Discussions](https://github.com/ali-ulu/levh/discussions).

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later).
